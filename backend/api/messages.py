import uuid
import pathlib
import re
from fastapi import APIRouter, HTTPException, UploadFile, File
from db import (get_db, write_connect, get_messages, get_message_meta, update_message, soft_delete_message,
                      toggle_reaction, get_reactions_for_message, get_reactions_for_group,
                      pin_message, unpin_message, get_pinned_messages)
from ws_manager import manager
from models import EditMessageRequest, ReactionRequest

UPLOAD_DIR = pathlib.Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml",
    "application/pdf", "text/plain", "application/json",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

router = APIRouter()


@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"不支持的文件类型: {file.content_type}")
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件大小超过 10MB 限制")
    ext = pathlib.Path(file.filename or "file").suffix
    filename = f"{uuid.uuid4()}{ext}"
    (UPLOAD_DIR / filename).write_bytes(contents)
    return {"url": f"/uploads/{filename}", "name": file.filename,
            "size": len(contents), "type": file.content_type}


@router.get("/api/groups/{group_id}/pins")
async def get_pins(group_id: int):
    async with get_db() as db:
        return await get_pinned_messages(db, group_id)


@router.post("/api/groups/{group_id}/messages/{msg_id}/pin")
async def pin_msg(group_id: int, msg_id: int):
    async with get_db() as db:
        await pin_message(db, group_id, msg_id)
        pins = await get_pinned_messages(db, group_id)
    await manager.broadcast(group_id, {"type": "pins_updated", "pins": pins})
    return {"ok": True}


@router.delete("/api/groups/{group_id}/messages/{msg_id}/pin")
async def unpin_msg(group_id: int, msg_id: int):
    async with get_db() as db:
        await unpin_message(db, group_id, msg_id)
        pins = await get_pinned_messages(db, group_id)
    await manager.broadcast(group_id, {"type": "pins_updated", "pins": pins})
    return {"ok": True}


@router.put("/api/messages/{msg_id}")
async def edit_message(msg_id: int, req: EditMessageRequest):
    async with write_connect() as db:
        meta = await get_message_meta(db, msg_id)
        if not meta or meta["member_id"] != req.member_id:
            raise HTTPException(403, "无权编辑此消息")
        await update_message(db, msg_id, req.content)
    await manager.broadcast(meta["group_id"], {"type": "message_edited", "id": msg_id, "content": req.content})
    return {"ok": True}


@router.delete("/api/messages/{msg_id}")
async def withdraw_message(msg_id: int, member_id: int):
    async with write_connect() as db:
        meta = await get_message_meta(db, msg_id)
        if not meta or meta["member_id"] != member_id:
            raise HTTPException(403, "无权撤回此消息")
        await soft_delete_message(db, msg_id)
    await manager.broadcast(meta["group_id"], {"type": "message_deleted", "id": msg_id})
    return {"ok": True}


@router.get("/api/groups/{group_id}/reactions")
async def get_group_reactions(group_id: int):
    async with get_db() as db:
        return await get_reactions_for_group(db, group_id)


@router.post("/api/messages/{msg_id}/reactions")
async def toggle_reaction_endpoint(msg_id: int, req: ReactionRequest):
    async with get_db() as db:
        meta = await get_message_meta(db, msg_id)
        if not meta:
            raise HTTPException(404, "Message not found")
        await toggle_reaction(db, msg_id, req.member_id, req.emoji)
        reactions = await get_reactions_for_message(db, msg_id)
    await manager.broadcast(meta["group_id"], {
        "type": "reaction_updated", "message_id": msg_id, "reactions": reactions
    })
    return {"ok": True}


@router.get("/api/members/{member_id}/unread")
async def get_unread_counts(member_id: int):
    async with get_db() as db:
        async with db.execute("""
            SELECT m.group_id, COUNT(m.id) as unread
            FROM messages m
            LEFT JOIN member_read mr ON mr.member_id = ? AND mr.group_id = m.group_id
            WHERE m.id > COALESCE(mr.last_read_id, 0)
            GROUP BY m.group_id
        """, (member_id,)) as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}


@router.get("/api/groups/{group_id}/messages/search")
async def search_group_messages(group_id: int, q: str, limit: int = 30):
    if not q.strip():
        return []
    async with get_db() as db:
        async with db.execute("""
            SELECT m.id, m.group_id, m.member_id, m.content, m.created_at,
                   mb.name, mb.type, mb.avatar_color
            FROM messages m
            JOIN members mb ON m.member_id = mb.id
            WHERE m.group_id = ? AND m.content LIKE ?
            ORDER BY m.id DESC LIMIT ?
        """, (group_id, f"%{q}%", limit)) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        created_at = r[4]
        if created_at and "Z" not in created_at and "+" not in created_at:
            created_at = created_at.replace(" ", "T") + "Z"
        result.append({
            "id": r[0], "group_id": r[1], "member_id": r[2], "content": r[3],
            "created_at": created_at, "sender_name": r[5], "sender_type": r[6], "avatar_color": r[7]
        })
    return result


@router.get("/api/groups/{group_id}/messages")
async def get_group_messages(group_id: int, before_id: int = None, limit: int = 50):
    async with get_db() as db:
        msgs = await get_messages(db, group_id, limit=limit, before_id=before_id)
    return {"messages": msgs, "has_more": len(msgs) == limit}
