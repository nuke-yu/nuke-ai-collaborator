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
    """Placeholder — wired in Task 4.

    dispatcher: sync callable used ONLY in tests (e.g. list.append).
    In production, pass nothing — tasks created internally.
    """
    pass
