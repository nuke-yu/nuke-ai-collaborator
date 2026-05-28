"""
bus/engine.py — EventBus 核心

对应 OpenCode bus/index.ts 的 PubSub 双通道设计：
  - typed channel：每个 event type 独立 Queue 列表
  - wildcard channel：所有事件都推送（adapter 用这个）

两个发布接口：
  publish(event)             — typed event dataclass（orchestrator / main 用）
  broadcast(group_id, dict)  — 兼容旧接口（executors 的 ctx.broadcaster 用）
"""
import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Callable

log = logging.getLogger(__name__)


class Subscription:
    """异步迭代器 + 上下文管理器，退出时自动从 bus 取消订阅。"""

    def __init__(self, queue: asyncio.Queue, cleanup: Callable[[], None]):
        self._queue = queue
        self._cleanup = cleanup

    def __aiter__(self) -> AsyncIterator[dict]:
        return self

    async def __anext__(self) -> dict:
        return await self._queue.get()

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *_) -> None:
        self._cleanup()


class EventBus:
    """
    asyncio 版 PubSub，支持 typed + wildcard 双通道订阅。

    线程安全假设：所有调用均在同一 asyncio event loop 内。
    """

    def __init__(self) -> None:
        # event_type → list of subscriber queues
        self._typed: dict[str, list[asyncio.Queue]] = {}
        # wildcard：所有事件都推送
        self._wildcard: list[asyncio.Queue] = []

    # ── 发布 ──────────────────────────────────────────────────────────────────

    async def publish(self, event) -> None:
        """发布 typed event dataclass（必须有 .type 和 .group_id 属性）。"""
        payload = {"type": event.type, **dataclasses.asdict(event)}
        log.debug("bus.publish type=%s group=%s", event.type, payload.get("group_id"))
        await self._dispatch(event.type, payload)

    async def broadcast(self, group_id: int, payload: dict) -> None:
        """兼容接口：和 WSManager.broadcast() 签名相同，executors 无需改动。"""
        p = {"group_id": group_id, **payload}
        log.debug("bus.broadcast type=%s group=%s", p.get("type"), group_id)
        await self._dispatch(p.get("type", ""), p)

    async def _dispatch(self, event_type: str, payload: dict) -> None:
        # 先推 typed，再推 wildcard（snapshot 避免迭代中增删）
        for q in list(self._typed.get(event_type, [])):
            await q.put(payload)
        for q in list(self._wildcard):
            await q.put(payload)

    # ── 订阅 ──────────────────────────────────────────────────────────────────

    def subscribe(self, event_cls) -> Subscription:
        """typed 订阅：只收指定 event type 的事件。"""
        q: asyncio.Queue = asyncio.Queue()
        self._typed.setdefault(event_cls.type, []).append(q)

        def cleanup():
            try:
                self._typed[event_cls.type].remove(q)
            except (KeyError, ValueError):
                pass

        log.debug("bus.subscribe type=%s", event_cls.type)
        return Subscription(q, cleanup)

    def subscribe_all(self) -> Subscription:
        """wildcard 订阅：收全部事件（WS adapter 使用）。"""
        q: asyncio.Queue = asyncio.Queue()
        self._wildcard.append(q)

        def cleanup():
            try:
                self._wildcard.remove(q)
            except ValueError:
                pass

        log.debug("bus.subscribe_all")
        return Subscription(q, cleanup)

    @asynccontextmanager
    async def on(self, event_cls, callback):
        """便捷上下文管理器：在 async with 块内监听单个 event type。"""
        sub = self.subscribe(event_cls)
        async with sub:

            async def _run():
                async for event in sub:
                    try:
                        await callback(event)
                    except Exception:
                        log.exception("bus.on callback error type=%s", event_cls.type)

            task = asyncio.create_task(_run())
            try:
                yield
            finally:
                task.cancel()
