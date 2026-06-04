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

    if kind == "search":
        q = (msg.get("q") or "").strip()
        if not q:
            return []
        limit = int(msg.get("limit") or 30)
        async with db.get_db() as conn:
            # group DB is self-contained: read denormalized sender_* (no members JOIN)
            cur = await conn.execute(
                "SELECT id, group_id, member_id, content, created_at, "
                "       sender_name, sender_type, sender_avatar "
                "FROM messages WHERE group_id = ? AND content LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (gid, f"%{q}%", limit),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            created = r[4]
            if created and "Z" not in created and "+" not in created:
                created = created.replace(" ", "T") + "Z"
            out.append({"id": r[0], "group_id": r[1], "member_id": r[2], "content": r[3],
                        "created_at": created, "sender_name": r[5], "sender_type": r[6],
                        "avatar_color": r[7]})
        return out

    if kind == "reactions":
        async with db.get_db() as conn:
            return await db.get_reactions_for_group(conn, gid)

    if kind == "pins":
        async with db.get_db() as conn:
            return await db.get_pinned_messages(conn, gid)

    raise ValueError(f"unknown query kind: {kind!r}")
