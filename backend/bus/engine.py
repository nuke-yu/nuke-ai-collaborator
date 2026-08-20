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
import threading
import threading
import dataclasses
from runtime import tracing
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
        self._closed = False

    def __aiter__(self) -> AsyncIterator[dict]:
        return self

    async def __anext__(self) -> dict:
        return await self._queue.get()

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        """Idempotently unsubscribe this listener."""
        if not self._closed:
            self._closed = True
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
        self._lock = threading.Lock()
        self._lock = threading.Lock()

    # ── 发布 ──────────────────────────────────────────────────────────────────

    async def publish(self, event) -> None:
        """发布 typed event dataclass（必须有 .type 和 .group_id 属性）。"""
        payload = {"type": event.type, "trace_id": tracing.get_trace_id(), **dataclasses.asdict(event)}
        log.debug("bus.publish type=%s group=%s", event.type, payload.get("group_id"))
        await self._dispatch(event.type, payload)

    async def broadcast(self, group_id: int, payload: dict) -> None:
        """兼容接口：和 WSManager.broadcast() 签名相同，executors 无需改动。"""
        p = {"group_id": group_id, "trace_id": tracing.get_trace_id(), **payload}
        log.debug("bus.broadcast type=%s group=%s", p.get("type"), group_id)
        await self._dispatch(p.get("type", ""), p)

    async def _dispatch(self, event_type: str, payload: dict) -> None:
        # DFT-080: Non-blocking dispatch for UI events to prevent slow subscribers from stalling the bus.
        # For critical events (control flow), we use await put() to guarantee delivery (backpressure).
        # We run the put operations concurrently to avoid Head-of-Line blocking across subscribers.
        from bus.events import _critical_events
        is_critical = event_type in _critical_events
        
        put_tasks = []

        for q in list(self._typed.get(event_type, [])):
            if is_critical:
                put_tasks.append(q.put(payload))
            else:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    log.warning("bus: typed queue for %s is full, dropping message", event_type)
        for q in list(self._wildcard):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("bus: wildcard queue is full, dropping message")

        if put_tasks:
            await asyncio.gather(*put_tasks)

    # ── 订阅 ──────────────────────────────────────────────────────────────────

    def subscribe(self, event_cls, maxsize: int = 1000) -> Subscription:
        """typed 订阅：只收指定 event type 的事件。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._typed.setdefault(event_cls.type, []).append(q)

        def cleanup():
            with self._lock:
                try:
                    self._typed[event_cls.type].remove(q)
                except (KeyError, ValueError):
                    pass

        log.debug("bus.subscribe type=%s", event_cls.type)
        return Subscription(q, cleanup)

    def subscribe_all(self, maxsize: int = 1000) -> Subscription:
        """wildcard 订阅：收全部事件（WS adapter 使用）。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._wildcard.append(q)

        def cleanup():
            with self._lock:
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
