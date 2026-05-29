"""
core/runner.py — 编排层与执行层之间的胶水

唯一允许同时 import 两边的地方。它本身不做任何决策、也不重写执行逻辑：
  1. 拿 WorkUnit → 装配通用 ExecutionContext（broadcaster=bus，stream 事件自动过总线）
  2. 调 executor.run(ctx) → 执行层负责流式 / 落库 / token
  3. 把 result 交给 orchestrator.observe → 编排层算出 OrchestratorStep
  4. 按返回值施加副作用（广播系统消息 / WorkflowUpdate / 调度下一批单元）
"""
import asyncio

from db import get_db, get_members, get_messages, save_message
from bus import bus
from bus.events import WorkflowUpdate
from executors.base import ExecutionContext
from executors import registry as exec_registry


async def _post_system_msg(group_id: int, sender_bot_id: int, text: str) -> None:
    async with get_db() as db:
        ann_id = await save_message(db, group_id, sender_bot_id, text)
        recent = await get_messages(db, group_id, limit=3)
    saved = next((m for m in recent if m["id"] == ann_id), {})
    await bus.broadcast(group_id, {
        "type": "message", **saved,
        "sender_name": "工作流系统", "avatar_color": "#6366f1",
    })


async def apply_step(group_id: int, orch, step) -> None:
    """把 OrchestratorStep 翻译成副作用。编排层决定，runner 执行。"""
    for ann in step.announcements:
        await _post_system_msg(group_id, ann.sender_bot_id, ann.text)
    if step.broadcast_state:
        await bus.publish(WorkflowUpdate(group_id=group_id, **orch.snapshot(group_id)))
    if step.done:
        await bus.publish(WorkflowUpdate(group_id=group_id, active=False, done=True))
    for unit in step.next_units:
        asyncio.create_task(run_unit(group_id, unit, orch))


async def run_unit(group_id: int, unit, orch) -> None:
    """跑一个工作单元：通过 executor 执行，再把产出交回编排层。"""
    await asyncio.sleep(0.5)
    async with get_db() as db:
        members = await get_members(db, group_id)
        recent = await get_messages(db, group_id, limit=20)
    all_bots = [m for m in members if m.get("type") == "bot"]

    ctx = ExecutionContext(
        bot=unit.bot, group_id=group_id, user_message=unit.trigger_msg,
        sender={"name": "系统"}, history=recent,
        all_bots=all_bots, all_members=members, broadcaster=bus,
        workflow_suffix=unit.prompt_suffix,
    )
    result = await exec_registry.get(unit.executor_id).run(ctx)
    if not result.full_text:
        return
    step = orch.observe(group_id, unit.bot["id"], result.full_text)
    await apply_step(group_id, orch, step)
