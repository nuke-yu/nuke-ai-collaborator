"""
core/runner.py — 编排层与执行层之间的胶水

唯一允许同时 import 两边的地方。它本身不做任何决策、也不重写执行逻辑：
  1. 拿 WorkUnit → 装配通用 ExecutionContext（broadcaster=bus，stream 事件自动过总线）
  2. 调 executor.run(ctx) → 执行层负责流式 / 落库 / token
  3. 把 result 交给 orchestrator.observe → 编排层算出 OrchestratorStep
  4. 按返回值施加副作用（广播系统消息 / WorkflowUpdate / 调度下一批单元）
"""
import asyncio
import dataclasses
import json
import logging

from db import get_db, global_db, write_connect, get_members, get_messages, save_message
from bus import bus
from bus.events import WorkflowUpdate, WorkflowPaused
from core import bg, workflow_store
from executors.base import ExecutionContext
from core.orchestration.interaction import StandardInteraction
from executors import registry as exec_registry
from executors.compact import compress_history

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


_WORKFLOW_UPDATE_FIELDS = {f.name for f in dataclasses.fields(WorkflowUpdate)}


async def _publish_workflow_state(group_id: int, orch) -> None:
    snap = {k: v for k, v in orch.snapshot(group_id).items() if k in _WORKFLOW_UPDATE_FIELDS}
    await bus.publish(WorkflowUpdate(group_id=group_id, **snap))


async def _handle_workspace_action(group_id: int, action: str) -> None:
    """P0-3: Handle worktree lifecycle actions after task completion.

    Args:
        group_id: Group ID
        action: One of "promote", "discard", "retain"
            - "promote": Merge worktree changes into main branch
            - "discard": Delete worktree without merging
            - "retain": Keep worktree for manual inspection (no action)
    """
    if action == "retain":
        log.info("runner: workspace_action=retain for group %d, keeping worktree", group_id)
        return

    from workspace import layout as ws_layout
    from workspace.git_worktree import promote_worktree, remove_worktree

    group_dir = ws_layout.group_dir(group_id)
    worktrees_dir = group_dir / "worktrees"

    if not worktrees_dir.exists():
        log.debug("runner: no worktrees directory for group %d", group_id)
        return

    # Find all worktrees for this group
    for item in list(worktrees_dir.iterdir()):
        if not item.is_dir() or not item.name.startswith("task_"):
            continue

        task_id = item.name[5:]  # Remove "task_" prefix

        try:
            if action == "promote":
                log.info("runner: promoting worktree %s for group %d", task_id, group_id)
                await promote_worktree(group_id, task_id)
            elif action == "discard":
                log.info("runner: discarding worktree %s for group %d", task_id, group_id)
                await remove_worktree(group_id, task_id)
            else:
                log.warning("runner: unknown workspace_action=%s for group %d", action, group_id)
        except Exception as e:
            log.exception("runner: failed to %s worktree %s for group %d: %s",
                         action, task_id, group_id, e)


async def apply_step(group_id: int, orch, step) -> None:
    """把 OrchestratorStep 翻译成副作用。编排层决定，runner 执行。"""
    for ann in step.announcements:
        await _post_system_msg(group_id, ann.sender_bot_id, ann.text)
    if step.confirm_gate:
        await _post_confirm_gate(group_id, step.confirm_gate)
    if step.broadcast_state:
        await _publish_workflow_state(group_id, orch)
        blob = orch.serialize(group_id)
        if blob is not None:
            await workflow_store.save_state(
                group_id, getattr(orch, "orchestrator_id", "workflow_v1"), blob)
    if step.done:
        await bus.publish(WorkflowUpdate(group_id=group_id, active=False, done=True))
        await workflow_store.clear_state(group_id)
    
    # Decoupled Event Trigger: Publish WorkflowPaused event when workflow gets gated or finishes
    if step.confirm_gate or step.done:
        reason = "gate" if step.confirm_gate else "done"
        bg.spawn(bus.publish(WorkflowPaused(group_id=group_id, reason=reason)))

    # Handle explicit workflow_paused event (e.g., completion_signal_missing)
    if step.workflow_paused:
        bg.spawn(bus.publish(step.workflow_paused))

    # P0-3: Handle workspace_action for worktree lifecycle
    if step.workspace_action:
        bg.spawn(_handle_workspace_action(group_id, step.workspace_action))

    for unit in step.next_units:
        # DFT-025/027: hold a reference (no GC) + register to the group so a
        # user abort cancels the whole workflow chain, not just the dispatch.
        bg.spawn_group(group_id, run_unit(group_id, unit, orch))


async def run_unit(group_id: int, unit, orch) -> None:
    """同群 run 串行入口：每群一把锁，一次只跑一个 run，避免连发消息时多个 run
    并发互踩、输出交错、回复丢失。排队的 run 轮到自己时，_run_unit_body 会重新
    加载最新历史（含上一个 run 的产出）。不同群组各自一把锁，互不阻塞。

    apply_step → spawn_group(run_unit) 的工作流链不会死锁：spawn_group 是
    fire-and-forget，外层 run 在派发下一单元后即退出释放锁，下一个 run 再获取。"""
    async with bg.group_run_lock(group_id):
        await _run_unit_body(group_id, unit, orch)


async def _run_unit_body(group_id: int, unit, orch) -> None:
    """跑一个工作单元：通过 executor 执行，再把产出交回编排层。"""
    await asyncio.sleep(0.5)
    # CELL-04: members live in the CENTRAL db; messages live in the bound GROUP db.
    async with global_db() as cdb:
        members = await get_members(cdb, group_id)
    async with get_db() as db:
        recent = await get_messages(db, group_id, limit=20)
    all_bots = [m for m in members if m.get("type") == "bot"]

    start_time_str = getattr(orch, "start_time", lambda gid: None)(group_id)
    if start_time_str:
        def norm(ts):
            if not ts:
                return ""
            ts = ts.replace("T", " ")
            if "Z" in ts:
                ts = ts.split("Z")[0]
            if "+" in ts:
                ts = ts.split("+")[0]
            return ts.strip()
        normalized_start = norm(start_time_str)
        filtered = []
        pre_start_human_added = False
        post_start_human_seen = False
        # reversed(recent) walks newest→oldest, so all post-start messages are
        # visited before any pre-start one.
        for msg in reversed(recent):
            created_at = msg.get("created_at") or ""
            if norm(created_at) >= normalized_start:
                if msg.get("sender_type") == "human":
                    post_start_human_seen = True
                filtered.append(msg)
            elif (msg.get("sender_type") == "human"
                  and not pre_start_human_added
                  and not post_start_human_seen):
                # Cold-start only: when the discussion has no post-start human
                # message yet (workflow kicked off without a fresh human prompt),
                # keep the single most-recent pre-start human message as minimal
                # context. Once the current topic already has a human message,
                # the stale pre-start line is redundant and must not bleed in —
                # otherwise yesterday's discussion topic leaks into today's.
                filtered.append(msg)
                pre_start_human_added = True
        recent = list(reversed(filtered))

        # Semantic viewpoint-based compression for older rounds
        viewpoints_summary = orch.get_viewpoints_cache(group_id)
        if viewpoints_summary is not None:
            pc = orch.participant_count(group_id)
            recent = await compress_history(recent, viewpoints_summary, members, all_bots, pc)

    ticket_id = unit.tag.get("ticket_id") if isinstance(unit.tag, dict) else None
    temp_ticket_id = None
    if not ticket_id:
        import uuid
        temp_ticket_id = f"chat_{uuid.uuid4().hex[:8]}"
        ticket_id = temp_ticket_id

    ctx = ExecutionContext(
        bot=unit.bot, group_id=group_id, user_message=unit.trigger_msg,
        sender={"name": "系统"}, history=recent,
        all_bots=all_bots, all_members=members,
        workflow_suffix=unit.prompt_suffix,
        is_workflow=unit.is_workflow,
        active_ticket_id=ticket_id,
        # Side-effect dispatcher: broadcasts via the bus + persists via the writer.
        # (Executors default this themselves when None, but wiring it here makes the
        # workflow path explicit and testable.)
        interaction=StandardInteraction(),
    )
    use_sandbox = False
    try:
        try:
            if ticket_id:
                from workspace.git_worktree import create_worktree, use_worktree
                try:
                    worktree_path = await create_worktree(group_id, ticket_id)
                    use_sandbox = True
                except Exception as w_err:
                    log.warning(f"Failed to create worktree sandbox for {ticket_id}, falling back to direct workspace: {w_err}")
                    ctx.active_ticket_id = None
                    use_sandbox = False

                if use_sandbox:
                    with use_worktree(group_id, worktree_path):
                        result = await exec_registry.get(unit.executor_id).run(ctx)
                else:
                    result = await exec_registry.get(unit.executor_id).run(ctx)
            else:
                result = await exec_registry.get(unit.executor_id).run(ctx)
        finally:
            async def _cleanup_finally():
                # P0-3: Check if task was cancelled - if so, skip promotion
                # CancelledError means abort/retry, worktree should be discarded by workspace_action
                current_task = asyncio.current_task()
                if current_task and current_task.cancelled():
                    log.info(f"runner: task cancelled for group {group_id}, skipping worktree promotion")
                    return

                try:
                    from workspace import layout
                    worktrees_dir = layout.group_dir(group_id) / "worktrees"
                    if worktrees_dir.exists() and use_sandbox:
                        from integrations.jira import get_jira
                        from workspace.git_worktree import promote_worktree

                        if temp_ticket_id:
                            log.info(f"Promoting temporary chat worktree {temp_ticket_id} for group {group_id}")
                            try:
                                await promote_worktree(group_id, temp_ticket_id)
                            except Exception as pe:
                                log.exception(f"Failed to execute immediate promotion for temp chat {temp_ticket_id}: {pe}")
                                try:
                                    await _post_system_msg(group_id, 0, f"⚠️ [沙箱合并失败] 临时会话自动合并失败: {pe}。请手动处理冲突。")
                                except Exception:
                                    log.warning("runner: failed to post immediate promotion failure message for %s", temp_ticket_id, exc_info=True)

                        tickets = await get_jira().list_tickets(group_id)
                        status_by_id = {t["ticket_id"]: t["status"] for t in tickets}

                        for item in list(worktrees_dir.iterdir()):
                            if item.is_dir() and item.name.startswith("task_"):
                                tid = item.name[5:]
                                if status_by_id.get(tid) == "done":
                                    log.info(f"Draining deferred promotion for task {tid} in group {group_id}")
                                    try:
                                        await promote_worktree(group_id, tid)
                                    except Exception as pe:
                                        log.exception(f"Failed to execute deferred promotion for task {tid}: {pe}")
                                        try:
                                            await _post_system_msg(group_id, 0, f"⚠️ [工作流系统错误] 工单 {tid} 自动合并失败: {pe}。请手动处理冲突。")
                                        except Exception:
                                            log.warning("runner: failed to post deferred promotion failure message for %s", tid, exc_info=True)
                except Exception as drain_err:
                    log.exception(f"Failed to execute group promotion drain: {drain_err}")

            # Note: asyncio.shield is a best-effort soft protection. In case of a second cancellation
            # (e.g. during a hard process shutdown), the shielded task can still be orphaned and
            # destroyed. This is acceptable for cleanups, but should not be relied upon for absolute,
            # crash-proof transaction guarantees.
            await asyncio.shield(_cleanup_finally())
    except Exception as e:
        log.exception("Workflow execution failed for group %d", group_id)
        from ai.client import AIError
        if isinstance(e, AIError):
            error_msg = f"[AI 服务异常] Bot「{unit.bot.get('name', 'Unknown')}」调用失败: {e}"
            await _post_system_msg(group_id, unit.bot.get("id", 0), error_msg)
            await bus.publish(WorkflowPaused(
                group_id=group_id,
                reason="provider_unavailable",
                details=str(e)
            ))
            return
        error_msg = f"[工作流系统错误] Bot「{unit.bot.get('name', 'Unknown')}」执行异常: {e}"
        await _post_system_msg(group_id, unit.bot.get("id", 0), error_msg)
        await bus.publish(WorkflowUpdate(group_id=group_id, active=False, done=False))
        await workflow_store.clear_state(group_id)
        return

    if not result.full_text:
        return
    try:
        step = orch.observe(group_id, unit.bot["id"], result.full_text, signals=getattr(result, "signals", None))
    except TypeError:
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
        await _publish_workflow_state(group_id, orch)
        for unit in orch.resume_units(group_id):
            if unit.executor_id == "tool_loop_v1":
                log.info("workflow group %s: skip resume of tool_loop_v1 unit (handled by recover_all)",
                         group_id)
                continue
            bg.spawn_group(group_id, run_unit(group_id, unit, orch))
