import json
import os
import shutil
import logging
from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from db import (get_db, write_connect, get_group, get_members, get_all_messages, get_member_stats,
                      update_member_setting, update_member_full, clear_bot_context)
from ws_manager import manager
from models import AddMemberRequest, CreateGroupRequest, UpdateGroupRequest
from workspace import init_bot_workspace, init_group_workspace

log = logging.getLogger(__name__)
router = APIRouter()


async def _try_exec(conn, sql, params):
    """Best-effort DELETE that tolerates a missing table/column across DB variants."""
    try:
        await conn.execute(sql, params)
    except Exception:
        pass


# Rows owned by / referencing a member. Split so we can run the right set against
# the central DB (member row + central FK refs) vs the per-group private DB
# (messages / sessions / reactions / read state).
_CENTRAL_REFS = (
    "DELETE FROM permission_rules WHERE bot_id=?",
    "DELETE FROM cron_jobs WHERE bot_id=?",
)
_MEMBER_DATA = (
    "DELETE FROM role_summaries WHERE bot_id=?",
    "DELETE FROM session_events WHERE session_id IN (SELECT id FROM agent_sessions WHERE bot_id=?)",
    "DELETE FROM agent_sessions WHERE bot_id=?",
    # Rows that FK-reference the member's MESSAGES must go before the messages
    # themselves (a reaction/pin on the member's message would otherwise block it).
    "DELETE FROM message_reactions WHERE message_id IN (SELECT id FROM messages WHERE member_id=?)",
    "DELETE FROM pinned_messages WHERE message_id IN (SELECT id FROM messages WHERE member_id=?)",
    "DELETE FROM message_embeddings WHERE message_id IN (SELECT id FROM messages WHERE member_id=?)",
    "DELETE FROM messages WHERE member_id=?",
    # The member's own reactions / read state (independent of the above).
    "DELETE FROM message_reactions WHERE member_id=?",
    "DELETE FROM member_read WHERE member_id=?",
)


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
    async with write_connect() as db:
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


@router.delete("/api/groups/{group_id}")
async def delete_group(group_id: int):
    """Fully purge a group: all members, their DB data, the group DB, and the on-disk workspace."""
    async with get_db() as db:
        group = await get_group(db, group_id)
        if not group:
            raise HTTPException(404, "Group not found")
        members = await get_members(db, group_id)

    # 1. Purge every member's central-DB data + the member row.
    async with write_connect() as db:
        for m in members:
            for sql in (*_CENTRAL_REFS, *_MEMBER_DATA):
                await _try_exec(db, sql, (m["id"],))
            await _try_exec(db, "DELETE FROM members WHERE id=?", (m["id"],))
        # Group-level central rows
        await _try_exec(db, "DELETE FROM groups WHERE id=?", (group_id,))
        await db.commit()

    # 2. Drop the group's private DB file.
    try:
        from runtime.dbpaths import group_db_path
        gpath = group_db_path(group_id)
        if os.path.exists(gpath):
            os.remove(gpath)
    except Exception:
        log.exception("delete_group: failed to remove group DB for %s", group_id)

    # 3. Remove on-disk workspace (shared + bots).
    try:
        from workspace import layout
        group_dir = layout.group_dir(group_id)
        if group_dir.exists():
            shutil.rmtree(group_dir)
    except Exception:
        log.exception("delete_group: failed to remove workspace for group %s", group_id)

    await manager.broadcast(group_id, {"type": "group_deleted", "id": group_id})
    return {"ok": True}


@router.put("/api/groups/{group_id}")
async def update_group(group_id: int, req: UpdateGroupRequest):
    async with write_connect() as db:
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
    async with write_connect() as db:
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
                "group_id": group_id,   # 关键：不传则落旧扁平区 workspaces/bot_{id}，而非 group_{id}/bots/
                "name": req.name,
                "role": req.role,
                "system_prompt": req.system_prompt,
                "personality_prompt": req.personality_prompt or "",
            })

        return {"id": bot_id, "name": req.name, "type": req.type}


@router.delete("/api/groups/{group_id}/members/{member_id}")
async def remove_member(group_id: int, member_id: int):
    """Fully purge a member: its row + all DB data + its on-disk workspace.

    Data is split across the central DB (member row, permission_rules, cron_jobs,
    role_summaries) and the group's private DB (messages, agent_sessions, reactions,
    read state); the workspace lives on disk under workspaces/bot_<id>/.
    """
    # 1. Central DB: FK-referencing rows + member-owned data + the member row.
    async with write_connect() as db:
        for sql in (*_CENTRAL_REFS, *_MEMBER_DATA):
            await _try_exec(db, sql, (member_id,))
        await db.execute("DELETE FROM members WHERE id=? AND group_id=?", (member_id, group_id))
        await db.commit()

    # 2. Group's private DB: messages / sessions / reactions / read state.
    try:
        from runtime.dbpaths import group_db_path
        gpath = group_db_path(group_id)
        if os.path.exists(gpath):
            async with write_connect(gpath) as gdb:
                for sql in _MEMBER_DATA:
                    await _try_exec(gdb, sql, (member_id,))
                await gdb.commit()
    except Exception:
        log.exception("remove_member: group-db cleanup failed for member %s", member_id)

    # 3. Filesystem: the bot's private workspace directory.
    try:
        from skills.constants import WORKSPACE_ROOT
        shutil.rmtree(os.path.join(str(WORKSPACE_ROOT), f"bot_{member_id}"), ignore_errors=True)
    except Exception:
        log.exception("remove_member: workspace cleanup failed for member %s", member_id)

    # Keep other connected clients' member lists in sync.
    await manager.broadcast(group_id, {"type": "member_removed", "member_id": member_id})
    return {"ok": True}


@router.put("/api/members/{member_id}")
async def update_member(member_id: int, body: dict):
    async with write_connect() as db:
        if 'auto_reply' in body and len(body) == 1:
            await update_member_setting(db, member_id, body.get("auto_reply"))
        else:
            await update_member_full(db, member_id, body)
    return {"ok": True}


@router.delete("/api/members/{member_id}/context")
async def clear_context(member_id: int, group_id: int):
    async with write_connect() as db:
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


@router.get("/api/groups/{group_id}/recap")
async def get_group_recap(group_id: int):
    async with get_db() as db:
        group = await get_group(db, group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    return {"group_id": group_id, "away_summary": group.get("away_summary")}


@router.delete("/api/groups/{group_id}/recap")
async def delete_group_recap(group_id: int):
    from core.recap import clear_recap
    await clear_recap(group_id)
    return {"ok": True}


@router.post("/api/groups/{group_id}/recap/trigger")
async def trigger_group_recap(group_id: int):
    from core.recap import generate_and_cache_recap
    # 用户手动触发 → force 跳过去抖；若仍被在途守卫跳过返回 None，回退到现有缓存，
    # 不要把前端 banner 清空。
    summary = await generate_and_cache_recap(group_id, force=True)
    if summary is None:
        async with get_db() as db:
            group = await get_group(db, group_id)
        summary = group.get("away_summary") if group else None
    return {"ok": True, "away_summary": summary}


@router.get("/api/groups/{group_id}/recap/personal/{member_id}")
async def get_personal_recap(group_id: int, member_id: int):
    # 方案 1：按需 per-user recap —— 概括该成员离开期间（last_read 之后）错过的消息。
    # 不缓存、不广播；成员频繁切标签的防抖交给前端。
    from core.recap import generate_personal_recap
    return await generate_personal_recap(group_id, member_id)


@router.post("/api/groups/{group_id}/suggest")
async def get_suggestions(group_id: int, payload: dict = None):
    from core.suggest import get_ai_suggestions
    payload = payload or {}
    awaiting_confirm = payload.get("awaiting_confirm")
    suggestions = await get_ai_suggestions(group_id, awaiting_confirm)
    return {"suggestions": suggestions}


