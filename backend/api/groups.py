import json
from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from db import (get_db, get_group, get_members, get_all_messages, get_member_stats,
                      update_member_setting, update_member_full, clear_bot_context)
from ws_manager import manager
from models import AddMemberRequest, CreateGroupRequest, UpdateGroupRequest
from workspace import init_bot_workspace, init_group_workspace

router = APIRouter()


@router.get("/api/groups")
async def get_all_groups():
    async with get_db() as db:
        async with db.execute("""
            SELECT g.id, g.name, g.created_at, COUNT(m.id) as member_count
            FROM groups g
            LEFT JOIN members m ON m.group_id = g.id
            GROUP BY g.id ORDER BY g.id
        """) as cur:
            rows = await cur.fetchall()
    return [{"id": r[0], "name": r[1], "created_at": r[2], "member_count": r[3]} for r in rows]


@router.post("/api/groups")
async def create_group(req: CreateGroupRequest):
    async with get_db() as db:
        async with db.execute("INSERT INTO groups (name) VALUES (?)", (req.name,)) as cur:
            group_id = cur.lastrowid
        await db.commit()
    await init_group_workspace(group_id, req.name)
    return {"id": group_id, "name": req.name}


@router.get("/api/groups/{group_id}")
async def get_group_info(group_id: int):
    async with get_db() as db:
        group = await get_group(db, group_id)
        if not group:
            raise HTTPException(404, "Group not found")
        members = await get_members(db, group_id)
    return {"group": group, "members": members}


@router.put("/api/groups/{group_id}")
async def update_group(group_id: int, req: UpdateGroupRequest):
    async with get_db() as db:
        if req.name is not None:
            name = req.name.strip()
            if not name:
                raise HTTPException(400, "群组名不能为空")
            await db.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
        if req.announcement is not None:
            await db.execute("UPDATE groups SET announcement=? WHERE id=?",
                             (req.announcement.strip() or None, group_id))
        await db.commit()
        group = await get_group(db, group_id)
    await manager.broadcast(group_id, {
        "type": "group_updated", "id": group_id,
        "name": group["name"], "announcement": group["announcement"]
    })
    return {"ok": True}


@router.post("/api/groups/{group_id}/members")
async def add_member(group_id: int, req: AddMemberRequest):
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM members WHERE group_id = ? AND name = ? AND type = ?",
            (group_id, req.name, req.type)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return {"id": existing[0], "name": req.name, "type": req.type}
        config_str = json.dumps(req.executor_config or {})
        async with db.execute(
            """INSERT INTO members (
                group_id, name, type, role, system_prompt, avatar_color,
                model_provider, model_name, temperature, max_tokens,
                personality_prompt, executor_id, executor_config, done_keyword
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (group_id, req.name, req.type, req.role, req.system_prompt, req.avatar_color,
             req.model_provider, req.model_name, req.temperature, req.max_tokens,
             req.personality_prompt or None, req.executor_id, config_str, req.done_keyword or None)
        ) as cur:
            await db.commit()
            bot_id = cur.lastrowid

        if req.type == "bot":
            await init_bot_workspace({
                "id": bot_id,
                "name": req.name,
                "role": req.role,
                "system_prompt": req.system_prompt,
                "personality_prompt": req.personality_prompt or "",
            })

        return {"id": bot_id, "name": req.name, "type": req.type}


@router.delete("/api/groups/{group_id}/members/{member_id}")
async def remove_member(group_id: int, member_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM members WHERE id=? AND group_id=?", (member_id, group_id))
        await db.commit()
    return {"ok": True}


@router.put("/api/members/{member_id}")
async def update_member(member_id: int, body: dict):
    async with get_db() as db:
        if 'auto_reply' in body and len(body) == 1:
            await update_member_setting(db, member_id, body.get("auto_reply"))
        else:
            await update_member_full(db, member_id, body)
    return {"ok": True}


@router.delete("/api/members/{member_id}/context")
async def clear_context(member_id: int, group_id: int):
    async with get_db() as db:
        await clear_bot_context(db, member_id, group_id)
    return {"ok": True}


@router.get("/api/groups/{group_id}/stats")
async def group_stats(group_id: int):
    async with get_db() as db:
        return await get_member_stats(db, group_id)


@router.get("/api/groups/{group_id}/export")
async def export_group(group_id: int, format: str = "markdown"):
    async with get_db() as db:
        group = await get_group(db, group_id)
        messages = await get_all_messages(db, group_id)
    if not group:
        raise HTTPException(404, "Group not found")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = group["name"]

    if format == "json":
        content = json.dumps({"group": group, "exported_at": now, "messages": messages},
                             ensure_ascii=False, indent=2)
        filename, media_type = f"{name}.json", "application/json"
    else:
        lines = [f"# {name}", "", f"*导出时间：{now}　消息数量：{len(messages)}*", "", "---", ""]
        for msg in messages:
            if msg.get("is_deleted"):
                continue
            ts = ""
            if msg.get("created_at"):
                try:
                    ts = datetime.fromisoformat(msg["created_at"]).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    ts = msg["created_at"]
            lines.append(f"**{msg['sender_name']}** · {ts}")
            if msg.get("reply_to"):
                snippet = (msg["reply_to"].get("content") or "")[:50]
                lines.append(f"> ↩ {msg['reply_to']['sender_name']}: {snippet}")
            lines.append("")
            lines.append(msg.get("content") or "*[文件附件]*")
            lines.append("")
            lines.append("---")
            lines.append("")
        content = "\n".join(lines)
        filename, media_type = f"{name}.md", "text/markdown; charset=utf-8"

    encoded = quote(filename)
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )
