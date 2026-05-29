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


def reconstruct_messages(config: dict, events: list[dict]) -> list[dict]:
    """Rebuild the messages array from the session event log.

    Handles: session_start, llm_response, tool_result.
    Skips:   tool_call (WAL marker only), child_fork, child_join.
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
        # tool_call, child_fork, child_join → metadata only, not added to messages

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
        "messages": messages,
        "parent_id": session.get("parent_id"),
        "executor_id": session.get("executor_id", "tool_loop_v1"),
    }

    if dispatcher is not None:
        dispatcher(payload)
    else:
        asyncio.create_task(_dispatch_recovery(payload))


async def _dispatch_recovery(payload: dict) -> None:
    """Re-dispatch a recovered session into the normal orchestrator flow.

    Lazy imports to avoid circular dependency at module load time.
    """
    from db import get_db, get_member, get_members, get_messages
    from core.orchestrator import dispatch_bots
    from ws_manager import manager as ws_manager

    bot_id = payload["bot_id"]
    group_id = payload["group_id"]

    async with get_db() as db:
        bot = await get_member(db, bot_id)
        members = await get_members(db, group_id)
        history = await get_messages(db, group_id, limit=50)

    if not bot:
        log.warning("recovery: bot %d not found, skipping session %s", bot_id, payload["session_id"])
        await update_session_status(payload["session_id"], "failed")
        return

    system_sender = {
        "id": 0, "name": "系统恢复", "type": "system", "avatar_color": "#6b7280",
    }

    await ws_manager.broadcast(group_id, {
        "type": "message",
        "content": f"[系统] 正在恢复 {bot['name']} 的未完成任务…",
        "member_id": 0,
        "sender_name": "系统",
    })

    asyncio.create_task(dispatch_bots(
        group_id=group_id,
        bots=[bot],
        user_message=f"[任务恢复] {payload['config'].get('user_message', '')}",
        sender=system_sender,
        history=history,
        all_members=members,
    ))
