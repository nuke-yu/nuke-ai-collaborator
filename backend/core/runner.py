"""
core/runner.py — 编排层与执行层之间的胶水

唯一允许同时 import 两边的地方。它本身不做任何决策、也不重写执行逻辑：
  1. 拿 WorkUnit → 装配通用 ExecutionContext（broadcaster=bus，stream 事件自动过总线）
  2. 调 executor.run(ctx) → 执行层负责流式 / 落库 / token
  3. 把 result 交给 orchestrator.observe → 编排层算出 OrchestratorStep
  4. 按返回值施加副作用（广播系统消息 / WorkflowUpdate / 调度下一批单元）
"""
import asyncio
import json
import logging

from db import get_db, global_db, write_connect, get_members, get_messages, save_message
from bus import bus
from bus.events import WorkflowUpdate
from core import bg, workflow_store
from executors.base import ExecutionContext
from core.orchestration.interaction import StandardInteraction
from executors import registry as exec_registry

log = logging.getLogger(__name__)


async def _post_system_msg(group_id: int, sender_bot_id: int, text: str) -> None:
    # save_message is a WRITE — it must go through the serialized writer
    # (db.write_connect), not the plain read connection, or it bypasses the
    # per-DB write lock and re-introduces "database is locked" contention.
    async with write_connect() as db:
        ann_id = await save_message(db, group_id, sender_bot_id, text)
        recent = await get_messages(db, group_id, limit=3)
    saved = next((m for m in recent if m["id"] == ann_id), {})
    await bus.broadcast(group_id, {
        "type": "message", **saved,
        "sender_name": "工作流系统", "avatar_color": "#6366f1",
    })


async def _post_confirm_gate(group_id: int, gate: dict) -> None:
    """落库 + 广播一张人确认卡片（内联在消息流里，前端按 meta.kind 渲染按钮）。
    挂在触发该门的 bot 名下；meta 带 gate_id 供前端点「确认」时回传。"""
    bot_id = gate.get("bot_id") or 0
    label = gate.get("label", "请确认")
    meta = {"kind": "confirm_gate", "gate_id": gate.get("gate_id"),
            "stage_name": gate.get("stage_name", ""), "status": "pending"}
    async with write_connect() as db:
        mid = await save_message(db, group_id, bot_id, label, meta=meta)
        recent = await get_messages(db, group_id, limit=3)
    saved = next((m for m in recent if m["id"] == mid), {})
    await bus.broadcast(group_id, {"type": "message", **saved})


async def mark_gate_confirmed(group_id: int, gate_id: str) -> None:
    """把已确认的确认门卡片 meta.status 翻成 'confirmed'，让"已确认"态能扛过刷新
    （前端 MessageBubble 读 meta.status 渲染；点按钮只是本地乐观更新，刷新即丢）。

    纯卡片外观，best-effort：任何失败只告警、不影响 confirm 本身的推进。卡片由
    _post_confirm_gate 落库（建时 status='pending'），故标记 confirmed 收在同一模块。"""
    if not gate_id:
        return
    try:
        async with write_connect() as db:
            cur = await db.execute(
                "SELECT id, meta FROM messages WHERE group_id = ? AND meta LIKE ?",
                (group_id, f'%"{gate_id}"%'),
            )
            rows = await cur.fetchall()
            for msg_id, meta_str in rows:
                try:
                    meta = json.loads(meta_str) if meta_str else {}
                except (ValueError, TypeError):
                    continue
                if meta.get("kind") == "confirm_gate" and meta.get("gate_id") == gate_id:
                    meta["status"] = "confirmed"
                    await db.execute(
                        "UPDATE messages SET meta = ? WHERE id = ?",
                        (json.dumps(meta, ensure_ascii=False), msg_id),
                    )
            await db.commit()
    except Exception:
        log.warning("mark_gate_confirmed failed for gate %s (group %s)", gate_id, group_id, exc_info=True)


async def apply_step(group_id: int, orch, step) -> None:
    """把 OrchestratorStep 翻译成副作用。编排层决定，runner 执行。"""
    for ann in step.announcements:
        await _post_system_msg(group_id, ann.sender_bot_id, ann.text)
    if step.confirm_gate:
        await _post_confirm_gate(group_id, step.confirm_gate)
    if step.broadcast_state:
        await bus.publish(WorkflowUpdate(group_id=group_id, **orch.snapshot(group_id)))
        blob = orch.serialize(group_id)
        if blob is not None:
            await workflow_store.save_state(
                group_id, getattr(orch, "orchestrator_id", "workflow_v1"), blob)
    if step.done:
        await bus.publish(WorkflowUpdate(group_id=group_id, active=False, done=True))
        await workflow_store.clear_state(group_id)
    
    # Trigger recap generation ONLY when workflow gets gated (confirm_gate is raised) or finishes (done)
    if step.confirm_gate or step.done:
        from core.recap import generate_and_cache_recap
        bg.spawn(generate_and_cache_recap(group_id))

    for unit in step.next_units:
        # DFT-025/027: hold a reference (no GC) + register to the group so a
        # user abort cancels the whole workflow chain, not just the dispatch.
        bg.spawn_group(group_id, run_unit(group_id, unit, orch))


async def run_unit(group_id: int, unit, orch) -> None:
    """跑一个工作单元：通过 executor 执行，再把产出交回编排层。"""
    await asyncio.sleep(0.5)
    # CELL-04: members live in the CENTRAL db; messages live in the bound GROUP db.
    async with global_db() as cdb:
        members = await get_members(cdb, group_id)
    async with get_db() as db:
        recent = await get_messages(db, group_id, limit=20)
    all_bots = [m for m in members if m.get("type") == "bot"]

    ctx = ExecutionContext(
        bot=unit.bot, group_id=group_id, user_message=unit.trigger_msg,
        sender={"name": "系统"}, history=recent,
        all_bots=all_bots, all_members=members,
        workflow_suffix=unit.prompt_suffix,
        # Side-effect dispatcher: broadcasts via the bus + persists via the writer.
        # (Executors default this themselves when None, but wiring it here makes the
        # workflow path explicit and testable.)
        interaction=StandardInteraction(),
    )
    try:
        result = await exec_registry.get(unit.executor_id).run(ctx)
    except Exception as e:
        log.exception("Workflow execution failed for group %d", group_id)
        error_msg = f"[工作流系统错误] Bot「{unit.bot.get('name', 'Unknown')}」执行异常: {e}"
        await _post_system_msg(group_id, unit.bot.get("id", 0), error_msg)
        await bus.publish(WorkflowUpdate(group_id=group_id, active=False, done=False))
        await workflow_store.clear_state(group_id)
        return

    if not result.full_text:
        return
    step = orch.observe(group_id, unit.bot["id"], result.full_text)
    await apply_step(group_id, orch, step)


async def resume_workflows(group_id: int | None = None) -> None:
    """启动时恢复崩溃前在跑的工作流：还原编排器状态、广播快照、重新派发在飞单元。

    只重新派发无副作用的旧单元 —— tool_loop_v1 这类有副作用的执行器靠各自的 WAL
    （sessions.recover_all）单独恢复，这里不重复触发以免重复落库/调用工具。
    """
    from core.orchestration import registry as orch_registry
    import core.workflow as wf

    rows = await workflow_store.load_all_active(group_id=group_id)
    for row in rows:
        group_id = row["group_id"]
        orchestrator_id = row.get("orchestrator_id") or "workflow_v1"
        orch = orch_registry.get(orchestrator_id)
        try:
            orch.restore(group_id, row["state"])
        except Exception as e:
            log.error("workflow restore failed for group %s: %r", group_id, e)
            continue
        # Route subsequent live observe (check_and_advance) to the right orchestrator.
        wf.bind(group_id, orchestrator_id)
        await bus.publish(WorkflowUpdate(group_id=group_id, **orch.snapshot(group_id)))
        for unit in orch.resume_units(group_id):
            if unit.executor_id == "tool_loop_v1":
                log.info("workflow group %s: skip resume of tool_loop_v1 unit (handled by recover_all)",
                         group_id)
                continue
            bg.spawn_group(group_id, run_unit(group_id, unit, orch))
