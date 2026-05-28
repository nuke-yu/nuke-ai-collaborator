"""
bus/ — 消息总线模块

对外暴露：
  bus      — 全局 EventBus 单例
  publish  — 便捷函数，等价于 await bus.publish(event)

所有 event 类型从 bus.events 导入：
  from bus.events import StreamStart, StreamChunk, ...
"""
from bus.engine import EventBus
from bus.adapter import ws_adapter

bus = EventBus()


async def publish(event) -> None:
    await bus.publish(event)


__all__ = ["bus", "publish", "ws_adapter"]
