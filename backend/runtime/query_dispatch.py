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
        from core import media
        limit = int(msg.get("limit") or 50)
        async with db.get_db() as conn:
            msgs = await db.get_messages(
                conn, gid, limit=limit,
                before_id=msg.get("before_id"), after_id=msg.get("after_id"),
            )
        for m in msgs:
            media.presign_message(m)  # canonical /media ref in DB → fresh signed URL on read
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
        from core import media
        async with db.get_db() as conn:
            pins = await db.get_pinned_messages(conn, gid)
        for m in pins:
            media.presign_message(m)
        return pins

    raise ValueError(f"unknown query kind: {kind!r}")


async def dispatch_mutate(msg: dict) -> None:
    gid = msg["group_id"]
    action = msg.get("action")
    member_id = msg.get("member_id")
    msg_id = msg.get("msg_id")
    try:
        event = await _run_mutate(gid, action, member_id, msg_id, msg)
    except Exception:
        log.exception("query_dispatch: mutate=%s group=%s failed", action, gid)
        return
    if event is not None:
        await bus.broadcast(gid, event)


async def _run_mutate(gid: int, action: str, member_id, msg_id, msg: dict):
    if action == "toggle_reaction":
        async with db.write_connect() as conn:
            meta = await db.get_message_meta(conn, msg_id)
            if not meta:
                return None
            await db.toggle_reaction(conn, msg_id, member_id, msg.get("emoji"))
            reactions = await db.get_reactions_for_message(conn, msg_id)
        return {"type": "reaction_updated", "message_id": msg_id, "reactions": reactions}

    if action == "pin":
        async with db.write_connect() as conn:
            await db.pin_message(conn, gid, msg_id)
            pins = await db.get_pinned_messages(conn, gid)
        return {"type": "pins_updated", "pins": pins}

    if action == "unpin":
        async with db.write_connect() as conn:
            await db.unpin_message(conn, gid, msg_id)
            pins = await db.get_pinned_messages(conn, gid)
        return {"type": "pins_updated", "pins": pins}

    if action == "edit":
        async with db.write_connect() as conn:
            meta = await db.get_message_meta(conn, msg_id)
            if not meta or meta["member_id"] != member_id:
                return None  # only the author may edit
            await db.update_message(conn, msg_id, msg.get("content"))
        return {"type": "message_edited", "id": msg_id, "content": msg.get("content")}

    if action == "withdraw":
        async with db.write_connect() as conn:
            meta = await db.get_message_meta(conn, msg_id)
            if not meta or meta["member_id"] != member_id:
                return None  # only the author may withdraw
            await db.soft_delete_message(conn, msg_id)
        return {"type": "message_deleted", "id": msg_id}

    raise ValueError(f"unknown mutate action: {action!r}")
