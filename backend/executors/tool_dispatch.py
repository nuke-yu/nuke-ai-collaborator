import asyncio
import logging

from executors import tool_executor

log = logging.getLogger(__name__)

# Keep strong refs to fire-and-forget L1 recording tasks so the event loop
# doesn't GC them mid-flight (asyncio only holds weak refs to bare tasks).
_recording_tasks: set = set()


def _record_event_l1(name: str, arguments: dict, result: str, is_error: bool, context: dict) -> None:
    """L1 — fire-and-forget the deterministic tool-event log. NEVER blocks or
    raises into the dispatch path: any failure stays inside record_event (which
    swallows everything except a genuine missing-schema migration gap)."""
    context = context or {}
    group_id = context.get("group_id")
    if group_id is None:
        return  # minimal/test loop without group scope — nothing to record
    try:
        thread_id = None
        try:
            import core.workflow as _wf
            thread_id = _wf.current_thread_id(group_id)
        except Exception:
            log.warning("tool_dispatch: failed to resolve current thread id for group %s", group_id, exc_info=True)
        from ai.tool_events import record_event
        task = asyncio.create_task(record_event(
            group_id=group_id,
            bot_id=context.get("bot_id"),
            tool=name,
            arguments=arguments,
            result=result,
            is_error=is_error,
            thread_id=thread_id,
        ))
        _recording_tasks.add(task)
        task.add_done_callback(_recording_tasks.discard)
    except Exception:
        log.exception("tool_dispatch: failed to schedule tool event recording")


async def dispatch_tool(name: str, arguments: dict, context: dict) -> tuple[str, bool]:
    """Dispatch a tool call to the right executor, returning (result, is_error).

    Routing policy (deliberately NOT "everything through the router"):
      - Builtin / skill / shell tools (anything registered in tool_executor)
        stay on tool_executor.execute() so the global before-hooks — permission
        check + run_shell danger guard — still fire. The ToolRouter is
        first-match and would route run_shell → ShellProvider, skipping those
        hooks (a silent security regression).
      - MCP tools are NOT in tool_executor's registry, so route ONLY those
        through the router (→ McpClientToolProvider, which applies its own HIL
        gate + timeout). Guarded by has_providers() so a worker/test without
        MCP falls straight through to tool_executor.

    L1 event log: this is the single chokepoint both the serial and parallel
    executors funnel through, and it sees the post-dispatch (result, is_error)
    for EVERY tool — builtin AND MCP — so it's where the deterministic
    tool_events row is recorded (fire-and-forget; see _record_event_l1).
    """
    if not tool_executor.has_tool(name):
        from executors.tool_router import router as _tool_router
        if _tool_router.has_providers():
            result, is_error = await _tool_router.execute(name, arguments, context=context)
            _record_event_l1(name, arguments, result, is_error, context)
            return result, is_error
    result, is_error = await tool_executor.execute(name, arguments, context=context)
    _record_event_l1(name, arguments, result, is_error, context)
    return result, is_error


async def execute_tool_call(name: str, arguments: dict, context: dict) -> str:
    """String-only wrapper around dispatch_tool (used by the minimal test loop)."""
    res, _ = await dispatch_tool(name, arguments, context)
    return res
