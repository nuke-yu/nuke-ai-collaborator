# sessions/recovery.py
import asyncio
import logging
from sessions.store import (
    get_orphaned_sessions, get_events, update_session_status,
)

log = logging.getLogger(__name__)

IDEMPOTENT_TOOLS = frozenset({
    "read_file", "list_dir", "web_search", "think", "grep",
    "get_memory", "list_files",
})

# Event types that are recorded for metadata/WAL purposes but intentionally
# do NOT contribute a message during reconstruction. Anything seen that is
# neither handled below nor listed here is unexpected and gets logged.
_IGNORED_EVENT_TYPES = frozenset({"tool_call", "child_fork", "child_join"})


def reconstruct_messages(config: dict, events: list[dict]) -> list[dict]:
    """Rebuild the messages array from the session event log.

    Handles: session_start, llm_response, tool_result.
    Skips:   tool_call (WAL marker only), child_fork, child_join.
    Unknown event types are skipped with a warning — if a new event type is
    added without updating this function, recovery would silently drop it.
    """
    messages: list[dict] = [{"role": "system", "content": config.get("system_prompt", "")}]

    for ev in events:
        etype = ev["event_type"]
        p = ev["payload"]

        if etype == "session_start":
            messages.append({"role": "user", "content": p["user_content"]})

        elif etype == "llm_response":
            msg: dict = {"role": "assistant", "content": p.get("content", "")}
            if p.get("tool_calls"):
                msg["tool_calls"] = p["tool_calls"]
            messages.append(msg)

        elif etype == "tool_result":
            messages.append({
                "role": "tool",
                "tool_call_id": p["tool_call_id"],
                "name": p.get("tool_name", ""),
                "content": p["result"],
            })

        elif etype not in _IGNORED_EVENT_TYPES:
            log.warning(
                "reconstruct_messages: unknown event_type %r skipped during "
                "recovery — reconstructed conversation may be incomplete. "
                "Add a branch in reconstruct_messages or list it in "
                "_IGNORED_EVENT_TYPES.", etype,
            )

    return messages


async def recover_all(dispatcher=None) -> None:
    """Find all orphaned sessions and attempt to resume them.

    dispatcher: sync callable used ONLY in tests (e.g. list.append).
    In production, call with no arguments — tasks created via _dispatch_recovery.

    Recovery order: children (parent_id IS NOT NULL) before parents,
    sorted by created_at ASC so oldest are retried first.
    """
    orphans = await get_orphaned_sessions()
    if not orphans:
        return

    children = [s for s in orphans if s.get("parent_id")]
    parents  = [s for s in orphans if not s.get("parent_id")]

    for session in children + parents:
        await _recover_one(session, dispatcher)


async def _recover_one(session: dict, dispatcher) -> None:
    sid = session["id"]
    config = session["config"]
    events = await get_events(sid)

    # Detect dangling tool_call (written before execution, no matching tool_result)
    committed_results: set[str] = {
        e["payload"]["tool_call_id"]
        for e in events if e["event_type"] == "tool_result"
    }
    dangling = [
        e for e in events
        if e["event_type"] == "tool_call"
        and e["payload"]["tool_call_id"] not in committed_results
    ]

    if dangling:
        dangling_tool = dangling[0]["payload"]["tool_name"]
        if dangling_tool not in IDEMPOTENT_TOOLS:
            log.warning(
                "session %s has dangling side-effectful tool '%s', marking needs_review",
                sid, dangling_tool,
            )
            await update_session_status(sid, "needs_review")
            return
        # Idempotent tool: roll back to events before the dangling tool_call
        cutoff_id = dangling[0]["id"]
        events = [e for e in events if e["id"] < cutoff_id]

    messages = reconstruct_messages(config, events)

    await update_session_status(sid, "recovering")
    log.info("recovering session %s (%d events, %d messages)", sid, len(events), len(messages))

    payload = {
        "session_id": sid,
        "bot_id": session["bot_id"],
        "group_id": session["group_id"],
        "config": config,
        "user_message": session.get("user_message", ""),
        "messages": messages,
        "parent_id": session.get("parent_id"),
        "executor_id": session.get("executor_id", "tool_loop_v1"),
    }

    if dispatcher is not None:
        dispatcher(payload)
    else:
        asyncio.create_task(_dispatch_recovery(payload))


async def _dispatch_recovery(payload: dict) -> None:
    """Resume a recovered session by continuing its reconstructed messages.

    DFT-018: this is a dedicated recovery entry — it does NOT go through
    dispatch_bots (which rebuilds the conversation from group history and would
    re-run every already-completed side-effectful tool). Instead it hands the
    reconstructed WAL messages to the executor via ExecutionContext.resume_*,
    reusing the same session_id so the completed/failed writeback closes the
    orphan (DFT-019). Lazy imports avoid a circular dependency at load time.
    """
    from db import get_db, get_member, get_members, get_messages
    from executors import registry
    from executors.base import ExecutionContext
    from bus import bus
    from ws_manager import manager as ws_manager

    sid = payload["session_id"]
    bot_id = payload["bot_id"]
    group_id = payload["group_id"]

    async with get_db() as db:
        bot = await get_member(db, bot_id)
        members = await get_members(db, group_id)
        history = await get_messages(db, group_id, limit=50)

    if not bot:
        log.warning("recovery: bot %d not found, skipping session %s", bot_id, sid)
        await update_session_status(sid, "failed")
        return

    await ws_manager.broadcast(group_id, {
        "type": "message",
        "content": f"[系统] 正在恢复 {bot['name']} 的未完成任务…",
        "member_id": 0,
        "sender_name": "系统",
    })

    all_bots = [m for m in members if m["type"] == "bot"]
    system_sender = {
        "id": 0, "name": "系统恢复", "type": "system", "avatar_color": "#6b7280",
    }
    ctx = ExecutionContext(
        bot=bot,
        group_id=group_id,
        user_message=payload.get("user_message", ""),
        sender=system_sender,
        history=history,
        all_bots=all_bots,
        all_members=members,
        broadcaster=bus,
        resume_session_id=sid,
        resume_messages=payload["messages"],
    )
    try:
        result = await registry.get(payload.get("executor_id", "tool_loop_v1")).run(ctx)
    except Exception:
        log.exception("recovery: executor run failed for session %s", sid)
        await update_session_status(sid, "failed")
        return

    # Workflow coordination: _dispatch_recovery bypasses run_unit / check_and_advance,
    # so a recovered TOP-LEVEL session belonging to an active workflow stage would
    # otherwise leave the workflow stalled. Re-observe its output to advance the stage,
    # exactly as live dispatch does. Sub-agents (parent_id set) and sessions whose bot
    # is not the current participant are skipped. Requires the orchestrator state to be
    # restored first (main.py runs resume_workflows before recover_all).
    if result and result.full_text and not payload.get("parent_id"):
        import core.workflow as wf
        if wf.is_workflow_participant(group_id, bot_id):
            await wf.check_and_advance(group_id, result.full_text, bot_id)
