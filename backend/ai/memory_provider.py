"""可插拔记忆组件：bot 处理流程依赖 MemoryProvider 协议，而非直接 import ai.memory 的内部函数。

这样记忆单元可以被替换 / 禁用 / 按 bot·group 策略选择，而 tool_loop 无需感知 facts、
summaries、reflections 这三条内部 pipeline 的存在。

接缝只有三个动作（与 ai.memory 现有调用面一一对应）：
  - recall  : 读路径，turn 前取记忆文本注入 system prompt   (← get_memory_context)
  - observe : 写路径，turn 后一次性触发 ingest + summarize + reflect  (← add_to_chroma / maybe_summarize / maybe_reflect)
  - forget  : 生命周期，删除某 bot 全部记忆                  (← delete_bot_memory)

ChromaMemoryProvider 是默认实现，逐函数包装 ai.memory（行为不变）。NullMemoryProvider 全 no-op，
用于禁用记忆。未来跨进程/远程记忆只需再实现一个同协议的类，tool_loop 一行不改。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryContext:
    """读路径入参：一个对象装齐 recall 需要的全部字段。"""
    bot_id: int
    group_id: int | None
    role: str            # 原始 bot role，可能为空串
    query: str
    history: list | None = None


@dataclass(frozen=True)
class MemoryEvent:
    """写路径入参：一个对象装齐 observe 需要的全部字段。"""
    bot_id: int
    group_id: int | None
    role: str            # 原始 bot role，可能为空串
    bot_name: str        # 供 summarize / reflect 在 role 为空时回退（保持既有语义）
    message_id: int
    text: str
    provider: str
    model: str


@runtime_checkable
class MemoryProvider(Protocol):
    async def recall(self, ctx: MemoryContext) -> str: ...
    async def observe(self, ev: MemoryEvent) -> None: ...
    async def forget(self, bot_id: int, group_id: int | None) -> None: ...


class NullMemoryProvider:
    """禁用记忆：读返回空串，写/删 no-op。bot 循环完全无需感知。"""

    async def recall(self, ctx: MemoryContext) -> str:
        return ""

    async def observe(self, ev: MemoryEvent) -> None:
        return None

    async def forget(self, bot_id: int, group_id: int | None) -> None:
        return None


class ChromaMemoryProvider:
    """默认实现：包装现有 ai.memory 模块函数。

    写路径编排（哪些 pipeline 在 turn 后跑）从调用方内化到 observe 内部——这正是解耦的核心：
    tool_loop 只发一个 observe，由组件自己决定触发 ingest + summarize + reflect。
    三条子 pipeline 并发执行、互不阻塞；单条失败被隔离记日志，不影响其余（与原先三个独立
    bg.spawn 的容错语义一致）。
    """

    async def recall(self, ctx: MemoryContext) -> str:
        from ai.memory import get_memory_context
        return await get_memory_context(
            ctx.bot_id, ctx.role or "", ctx.query, ctx.group_id, ctx.history
        )

    async def observe(self, ev: MemoryEvent) -> None:
        from ai.memory import add_to_chroma, maybe_summarize, maybe_reflect
        # 既有语义：ingest 用 role（可空）；summarize / reflect 在 role 空时回退到 bot_name。
        ingest_role = ev.role or ""
        compact_role = ev.role or ev.bot_name
        results = await asyncio.gather(
            add_to_chroma(ev.message_id, ev.text, ingest_role, ev.bot_id, ev.group_id, ev.provider, ev.model),
            maybe_summarize(ev.group_id, ev.bot_id, compact_role, [ev.bot_id]),
            maybe_reflect(ev.group_id, ev.bot_id, compact_role, ev.provider, ev.model),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                log.error("ChromaMemoryProvider.observe: sub-pipeline failed", exc_info=r)

    async def forget(self, bot_id: int, group_id: int | None) -> None:
        from ai.memory import delete_bot_memory
        await delete_bot_memory(bot_id, group_id)


# 单例：两种 provider 均无状态，复用即可，省去每轮构造。
_CHROMA = ChromaMemoryProvider()
_NULL = NullMemoryProvider()

_DISABLED_POLICIES = {"off", "none", "null", "disabled", "false"}


def get_memory_provider(bot: dict | None = None) -> MemoryProvider:
    """按 bot 的 executor_config.memory 策略选择实现。

    缺省 / 未识别值 → ChromaMemoryProvider（现有行为）；显式关闭 → NullMemoryProvider。
    群组隔离天然成立：provider 无状态，隔离由底层 ai.memory 的 group_id 过滤保证。
    """
    policy = "chroma"
    if bot:
        policy = str((bot.get("executor_config") or {}).get("memory", "chroma")).lower()
    return _NULL if policy in _DISABLED_POLICIES else _CHROMA
