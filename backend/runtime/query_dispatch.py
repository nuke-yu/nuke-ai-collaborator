"""Worker-side handlers for group-domain reads (query) and writes (mutate).

These run inside the worker that owns the group's private DB — the caller
(Worker._handle) has already bound it via db.bind_db, exactly like USER_MESSAGE.
A read publishes a `query_result` bus event (correlated by req_id); a write
publishes the same update event the old HTTP endpoint broadcast. Both flow up
through Worker._pump_upstream as `broadcast` frames and fan out to browsers —
the supervisor never touches the group DB.
"""
import logging

import db
from bus import bus

log = logging.getLogger(__name__)


async def dispatch_query(msg: dict) -> None:
    gid = msg["group_id"]
    req_id = msg.get("req_id")
    kind = msg.get("query")
    try:
        data = await _run_query(gid, kind, msg)
        result = {"type": "query_result", "req_id": req_id, "ok": True, "data": data}
    except Exception as e:
        log.exception("query_dispatch: query=%s group=%s failed", kind, gid)
        result = {"type": "query_result", "req_id": req_id, "ok": False, "error": str(e)}
    await bus.broadcast(gid, result)


async def _run_query(gid: int, kind: str, msg: dict):
    if kind == "messages":
        limit = int(msg.get("limit") or 50)
        async with db.get_db() as conn:
            msgs = await db.get_messages(
                conn, gid, limit=limit,
                before_id=msg.get("before_id"), after_id=msg.get("after_id"),
            )
        return {"messages": msgs, "has_more": len(msgs) == limit}
    raise ValueError(f"unknown query kind: {kind!r}")
