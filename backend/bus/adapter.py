"""
bus/adapter.py — WebSocket 推送适配层

整个项目唯一知道 WSManager 的地方（除了 main.py 的连接管理）。

流程：
  bus.subscribe_all() → 收到 payload → 取出 group_id → manager.broadcast()

启动方式（在 main.py lifespan 里）：
  asyncio.create_task(ws_adapter())
"""
import asyncio
import logging

from bus.engine import EventBus
from ws_manager import manager

log = logging.getLogger(__name__)


async def ws_adapter(bus: EventBus) -> None:
    """持续运行的后台任务：把所有 bus 事件推送到对应 group 的 WS clients。"""
    log.info("bus ws_adapter started")
    async with bus.subscribe_all() as sub:
        async for payload in sub:
            group_id = payload.get("group_id")
            if group_id is None:
                log.warning("bus event missing group_id, skipping: type=%s", payload.get("type"))
                continue
            # adapter 不向客户端暴露内部字段
            out = {k: v for k, v in payload.items() if k != "group_id"}
            try:
                await manager.broadcast(group_id, out)
            except Exception:
                log.exception("ws_adapter broadcast failed group=%s type=%s", group_id, payload.get("type"))
