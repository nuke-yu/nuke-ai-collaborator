# sessions/recovery.py
import asyncio
import logging
from sessions.store import (
    get_orphaned_sessions, get_events, update_session_status,
)

log = logging.getLogger(__name__)

IDEMPOTENT_TOOLS = frozenset({
    "read_file", "list_dir", "web_search", "think", "search", "code_intel",
    "get_memory", "list_files",
})

def is_idempotent(tool_name: str) -> bool:
    if tool_name in IDEMPOTENT_TOOLS:
        return True
    import os
    env_val = os.environ.get("NUKE_IDEMPOTENT_TOOLS")
    if env_val:
        return tool_name in {t.strip() for t in env_val.split(",") if t.strip()}
    return False


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


async def recover_all(dispatcher=None, group_id: int | None = None) -> None:
    """Chat semantics: a bot run interrupted by a crash / network drop is ABANDONED,
    not resumed.

    Workflows: when the bot is a participant in an active workflow stage, we auto-resume
    the session seamlessly without prompting the user, using reconstructed messages
    and rolling back any dangling idempotent tools.
    """
    orphans = await get_orphaned_sessions(group_id=group_id)
    for session in orphans:
        # Check if the bot is currently a workflow participant
        import core.workflow as wf
        is_wf = False
        try:
            is_wf = wf.is_workflow_participant(session["group_id"], session["bot_id"])
        except Exception:
            log.exception(
                "recover_all: failed to resolve workflow participation for session %s",
                session["id"],
            )

        if is_wf:
            sid = session["id"]
            log.info("recover_all: auto-resuming active workflow session %s for bot %d", sid, session["bot_id"])
            
            # Reconstruct session messages and handle dangling tool calls
            config = session["config"]
            events = await get_events(sid)
            
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
                if not is_idempotent(dangling_tool):
                    log.warning(
                        "workflow session %s has dangling side-effectful tool '%s', marking needs_review",
                        sid, dangling_tool,
                    )
                    await update_session_status(sid, "needs_review")
                    continue
                # Idempotent tool / mock tool: roll back to events before the dangling tool_call
                cutoff_id = dangling[0]["id"]
                events = [e for e in events if e["id"] < cutoff_id]
                
            messages = reconstruct_messages(config, events)
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
            await update_session_status(sid, "recovering")
            from core import bg
            bg.spawn(_dispatch_recovery(payload))
        else:
            log.info("recover_all: marking chat session %s failed", session["id"])
            await update_session_status(session["id"], "failed")


async def _recover_one(session: dict, dispatcher) -> None:
    sid = session["id"]
    config = session["config"]
    group_id = session["group_id"]
    bot_id = session["bot_id"]
    
    # Point 2: Use full snapshot if available, otherwise reconstruct from events
    snapshot_json = session.get("last_snapshot_json")
    if snapshot_json:
        import json
        messages = json.loads(snapshot_json)
        log.info("found full snapshot for session %s, using it for recovery", sid)
    else:
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
            if not is_idempotent(dangling_tool):
                log.warning(
                    "session %s has dangling side-effectful tool '%s', marking needs_review",
                    sid, dangling_tool,
                )
                await update_session_status(sid, "needs_review")
                return
            # Idempotent tool: roll back to events before the dangling tool_call
            cutoff_id = dangling[0]["id"]
            events = [e for e in events if e["id"] < cutoff_id]

    # Point 3: Notify group instead of auto-dispatching
    # We update status to 'awaiting_recovery' to mark it as found but not yet resumed
    await update_session_status(sid, "awaiting_recovery")
    
    from db import global_db, get_member
    async with global_db() as db:
        bot = await get_member(db, bot_id)
    
    bot_name = bot["name"] if bot else f"Bot {bot_id}"
    
    from ws_manager import manager as ws_manager
    await ws_manager.broadcast(group_id, {
        "type": "recovery_prompt",
        "session_id": sid,
        "bot_id": bot_id,
        "bot_name": bot_name,
        "user_message": session.get("user_message", "")[:200],
        "message": f"检测到 {bot_name} 有未完成的任务。是否继续执行？",
    })
    log.info("sent recovery prompt for session %s to group %s", sid, group_id)


async def resume_session(session_id: str) -> bool:
    """Public API to trigger resumption of a session that is awaiting_recovery."""
    from sessions.store import get_session
    session = await get_session(session_id)
    if not session or session["status"] != "awaiting_recovery":
        return False
    
    config = session["config"]
    snapshot_json = session.get("last_snapshot_json")
    if snapshot_json:
        import json
        messages = json.loads(snapshot_json)
    else:
        events = await get_events(session_id)
        messages = reconstruct_messages(config, events)

    payload = {
        "session_id": session_id,
        "bot_id": session["bot_id"],
        "group_id": session["group_id"],
        "config": config,
        "user_message": session.get("user_message", ""),
        "messages": messages,
        "parent_id": session.get("parent_id"),
        "executor_id": session.get("executor_id", "tool_loop_v1"),
    }
    
    await update_session_status(session_id, "recovering")
    # DFT-063: hold a reference + log exceptions instead of a bare create_task,
    # so this fire-and-forget recovery dispatch can't be GC'd mid-flight or
    # silently swallow its exception.
    from core import bg
    bg.spawn(_dispatch_recovery(payload))
    return True



async def _dispatch_recovery(payload: dict) -> None:
    """Resume a recovered session by continuing its reconstructed messages.

    DFT-018: this is a dedicated recovery entry — it does NOT go through
    dispatch_bots (which rebuilds the conversation from group history and would
    re-run every already-completed side-effectful tool). Instead it hands the
    reconstructed WAL messages to the executor via ExecutionContext.resume_*,
    reusing the same session_id so the completed/failed writeback closes the
    orphan (DFT-019). Lazy imports avoid a circular dependency at load time.
    """
    from db import get_db, global_db, get_member, get_members, get_messages
    from executors import registry
    from executors.base import ExecutionContext
    from core.orchestration.interaction import StandardInteraction
    from ws_manager import manager as ws_manager

    sid = payload["session_id"]
    bot_id = payload["bot_id"]
    group_id = payload["group_id"]

    async with global_db() as gdb:
        bot = await get_member(gdb, bot_id)
        members = await get_members(gdb, group_id)
    async with get_db() as db:
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
        interaction=StandardInteraction(),
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
