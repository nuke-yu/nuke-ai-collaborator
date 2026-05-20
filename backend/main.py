from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import quote
import json
import asyncio
import uuid
import pathlib
import re

from database import (init_db, get_db, get_group, get_members, get_messages, save_message,
                      get_member, update_message, soft_delete_message, get_message_meta,
                      toggle_reaction, get_reactions_for_message, get_reactions_for_group,
                      pin_message, unpin_message, get_pinned_messages,
                      get_all_messages, get_member_stats,
                      update_member_setting)
from ws_manager import manager
from ai_client import call_ai, call_ai_stream, AIError
from role_router import should_bot_respond, build_context_message
from models import AddMemberRequest, CreateGroupRequest, UpdateGroupRequest, RoleTemplateRequest, EditMessageRequest, ReactionRequest
from memory import maybe_summarize, get_memory_context, add_to_chroma

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
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

# group_id -> bot_id：记录当前哪个 bot 持有会话
active_bot: dict[int, int] = {}

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

@app.get("/api/config")
async def get_config():
    from config import read_config, _preview, FIELDS
    cfg = read_config()
    result = {}
    for field, env_var in FIELDS.items():
        import os
        val = cfg.get(field, "").strip() or os.getenv(env_var, "")
        result[field] = {"configured": bool(val), "preview": _preview(val)}
    return result

@app.put("/api/config")
async def save_config(data: dict):
    from config import read_config, write_config
    cfg = read_config()
    allowed = {"deepseek_api_key", "openai_api_key", "anthropic_api_key", "ollama_base_url"}
    for key, val in data.items():
        if key in allowed:
            cfg[key] = val.strip()
    write_config(cfg)
    return {"ok": True}

@app.get("/api/groups/{group_id}/pins")
async def get_pins(group_id: int):
    async with get_db() as db:
        return await get_pinned_messages(db, group_id)

@app.post("/api/groups/{group_id}/messages/{msg_id}/pin")
async def pin_msg(group_id: int, msg_id: int):
    async with get_db() as db:
        await pin_message(db, group_id, msg_id)
        pins = await get_pinned_messages(db, group_id)
    await manager.broadcast(group_id, {"type": "pins_updated", "pins": pins})
    return {"ok": True}

@app.delete("/api/groups/{group_id}/messages/{msg_id}/pin")
async def unpin_msg(group_id: int, msg_id: int):
    async with get_db() as db:
        await unpin_message(db, group_id, msg_id)
        pins = await get_pinned_messages(db, group_id)
    await manager.broadcast(group_id, {"type": "pins_updated", "pins": pins})
    return {"ok": True}

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"不支持的文件类型: {file.content_type}")
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件大小超过 10MB 限制")
    ext = pathlib.Path(file.filename or "file").suffix
    filename = f"{uuid.uuid4()}{ext}"
    (UPLOAD_DIR / filename).write_bytes(contents)
    return {"url": f"/uploads/{filename}", "name": file.filename, "size": len(contents), "type": file.content_type}

@app.get("/api/groups")
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

@app.get("/api/groups/{group_id}")
async def get_group_info(group_id: int):
    async with get_db() as db:
        group = await get_group(db, group_id)
        if not group:
            raise HTTPException(404, "Group not found")
        members = await get_members(db, group_id)
        return {"group": group, "members": members}

@app.put("/api/messages/{msg_id}")
async def edit_message(msg_id: int, req: EditMessageRequest):
    async with get_db() as db:
        meta = await get_message_meta(db, msg_id)
        if not meta or meta["member_id"] != req.member_id:
            raise HTTPException(403, "无权编辑此消息")
        await update_message(db, msg_id, req.content)
    await manager.broadcast(meta["group_id"], {
        "type": "message_edited", "id": msg_id, "content": req.content
    })
    return {"ok": True}

@app.delete("/api/messages/{msg_id}")
async def withdraw_message(msg_id: int, member_id: int):
    async with get_db() as db:
        meta = await get_message_meta(db, msg_id)
        if not meta or meta["member_id"] != member_id:
            raise HTTPException(403, "无权撤回此消息")
        await soft_delete_message(db, msg_id)
    await manager.broadcast(meta["group_id"], {
        "type": "message_deleted", "id": msg_id
    })
    return {"ok": True}

@app.get("/api/groups/{group_id}/reactions")
async def get_group_reactions(group_id: int):
    async with get_db() as db:
        return await get_reactions_for_group(db, group_id)

@app.post("/api/messages/{msg_id}/reactions")
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

@app.get("/api/members/{member_id}/unread")
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

@app.get("/api/groups/{group_id}/messages/search")
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
    return [{
        "id": r[0], "group_id": r[1], "member_id": r[2], "content": r[3],
        "created_at": r[4], "sender_name": r[5], "sender_type": r[6], "avatar_color": r[7]
    } for r in rows]

@app.get("/api/groups/{group_id}/messages")
async def get_group_messages(group_id: int, before_id: int = None, limit: int = 50):
    async with get_db() as db:
        msgs = await get_messages(db, group_id, limit=limit, before_id=before_id)
        return {"messages": msgs, "has_more": len(msgs) == limit}

ANALYST_PROMPT = """你是一位专业的需求分析师，只负责需求分析，绝不写任何代码。

工作流程：
1. 收到需求后，先梳理已理解的部分，列出所有不清楚的问题逐一向用户追问。
2. 根据用户每次回答，判断是否还有疑问，有则继续追问，无则汇总。
3. 只有当所有功能点、边界条件、优先级、异常处理都已明确确认后，才输出完整的需求规格文档。
4. 需求规格文档输出完毕后，在最后一行必须原样写出：「@开发工程师 所有需求已分析完毕，可以开始开发了。」
5. 在还有任何未确认的问题时，绝对不能写这句话。这句话一旦出现，开发就会自动启动，请慎重。

用中文回答，简洁专业。"""

DEVELOPER_PROMPT = """你是一位经验丰富的全栈开发工程师。接到开发任务后，根据需求规格编写完整的代码实现。回答实用，包含具体代码示例，用中文。

只有当所有功能代码完整实现后，在最后一行原样写出：「@测试工程师 代码开发完成，可以开始测试了。」"""

TESTER_PROMPT = """你是一位严格的QA测试工程师。接到测试任务后，设计完整的测试用例，执行测试并输出测试报告，用中文。

完成所有测试后，在最后一行原样写出交接信息（任务提示中会告诉你发起人名字）。"""

DEFAULT_BOTS = [
    ("需求分析师", "需求分析师", ANALYST_PROMPT, "#8b5cf6"),
    ("开发工程师", "开发工程师", DEVELOPER_PROMPT, "#3b82f6"),
    ("测试工程师", "测试工程师", TESTER_PROMPT, "#10b981"),
]

@app.post("/api/groups")
async def create_group(req: CreateGroupRequest):
    async with get_db() as db:
        async with db.execute("INSERT INTO groups (name) VALUES (?)", (req.name,)) as cur:
            group_id = cur.lastrowid
        await db.commit()
        return {"id": group_id, "name": req.name}

@app.put("/api/groups/{group_id}")
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

@app.post("/api/groups/{group_id}/members")
async def add_member(group_id: int, req: AddMemberRequest):
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM members WHERE group_id = ? AND name = ? AND type = ?",
            (group_id, req.name, req.type)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return {"id": existing[0], "name": req.name, "type": req.type}
        async with db.execute(
            "INSERT INTO members (group_id, name, type, role, system_prompt, avatar_color, model_provider, model_name) VALUES (?,?,?,?,?,?,?,?)",
            (group_id, req.name, req.type, req.role, req.system_prompt, req.avatar_color, req.model_provider, req.model_name)
        ) as cur:
            await db.commit()
            return {"id": cur.lastrowid, "name": req.name, "type": req.type}

@app.delete("/api/groups/{group_id}/members/{member_id}")
async def remove_member(group_id: int, member_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM members WHERE id=? AND group_id=?", (member_id, group_id))
        await db.commit()
        return {"ok": True}

@app.put("/api/members/{member_id}")
async def update_member(member_id: int, body: dict):
    async with get_db() as db:
        await update_member_setting(db, member_id, body.get("auto_reply"))
    return {"ok": True}

@app.get("/api/templates")
async def get_templates():
    async with get_db() as db:
        async with db.execute("SELECT * FROM role_templates ORDER BY id") as cur:
            rows = await cur.fetchall()
            return [{"id": r[0], "name": r[1], "role": r[2], "system_prompt": r[3], "avatar_color": r[4]} for r in rows]

@app.post("/api/templates")
async def create_template(req: RoleTemplateRequest):
    async with get_db() as db:
        async with db.execute(
            "INSERT INTO role_templates (name, role, system_prompt, avatar_color) VALUES (?,?,?,?)",
            (req.name, req.role, req.system_prompt, req.avatar_color)
        ) as cur:
            await db.commit()
            return {"id": cur.lastrowid, "name": req.name, "role": req.role, "system_prompt": req.system_prompt, "avatar_color": req.avatar_color}

@app.put("/api/templates/{template_id}")
async def update_template(template_id: int, req: RoleTemplateRequest):
    async with get_db() as db:
        await db.execute(
            "UPDATE role_templates SET name=?, role=?, system_prompt=?, avatar_color=? WHERE id=?",
            (req.name, req.role, req.system_prompt, req.avatar_color, template_id)
        )
        await db.commit()
        return {"id": template_id, "name": req.name, "role": req.role, "system_prompt": req.system_prompt, "avatar_color": req.avatar_color}

@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM role_templates WHERE id=?", (template_id,))
        await db.commit()
        return {"ok": True}

@app.get("/api/groups/{group_id}/stats")
async def group_stats(group_id: int):
    async with get_db() as db:
        return await get_member_stats(db, group_id)

@app.get("/api/groups/{group_id}/export")
async def export_group(group_id: int, format: str = "markdown"):
    async with get_db() as db:
        group = await get_group(db, group_id)
        messages = await get_all_messages(db, group_id)

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = group["name"]

    if format == "json":
        payload = {"group": group, "exported_at": now, "messages": messages}
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        filename = f"{name}.json"
        media_type = "application/json"
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
        filename = f"{name}.md"
        media_type = "text/markdown; charset=utf-8"

    encoded = quote(filename)
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )

async def auto_continue_if_needed(group_id: int, bot: dict, all_members: list, max_iter: int = 5):
    """开发类角色没写完（没有 @下一个人）时，自动续写直到完成"""
    if "开发" not in (bot["role"] or ""):
        return

    for _ in range(max_iter):
        async with get_db() as db:
            recent = await get_messages(db, group_id, limit=10)

        latest = next((m for m in reversed(recent) if m["member_id"] == bot["id"]), None)
        if not latest:
            break
        # 已经 @了某人 → 完成
        if any(f"@{m['name']}" in latest["content"] for m in all_members):
            break

        history, _ = build_context_message("", "系统", recent)
        system_prompt = bot["system_prompt"] or f"你是{bot['name']}，{bot['role']}。"
        memory = await get_memory_context(group_id, bot["role"] or "", "继续代码")
        if memory:
            system_prompt += f"\n\n{memory}"

        ai_reply, bot_msg_id = await stream_bot_response(
            group_id, bot, system_prompt, history,
            "请继续上面未完成的代码，直到所有功能全部实现完毕。完成后在最后一行写上交接信息。"
        )
        if not ai_reply:
            break
        asyncio.create_task(add_to_chroma(bot_msg_id, ai_reply, bot["role"] or "", group_id))
        asyncio.create_task(maybe_summarize(group_id, bot["role"] or bot["name"], [bot["id"]]))

        if any(f"@{m['name']}" in ai_reply for m in all_members):
            break


async def check_handoff(group_id: int, all_bots: list, all_members: list, context_messages: list, _depth: int = 0):
    """检测最新 bot 消息中的 @mention，自动通知对应角色开始工作，支持链式传递"""
    if _depth > 5:
        return

    async with get_db() as db:
        recent = await get_messages(db, group_id, limit=5)

    latest_bot_msg = next((m for m in reversed(recent) if m["sender_type"] == "bot"), None)
    if not latest_bot_msg:
        return

    content = latest_bot_msg["content"]
    sender_id = latest_bot_msg["member_id"]

    # 被 @提及的 bot（排除自身）
    mentioned_bots = [b for b in all_bots if f"@{b['name']}" in content and b["id"] != sender_id]

    if not mentioned_bots:
        # @了人类成员 → 流程结束，释放锁
        if any(f"@{m['name']}" in content for m in all_members if m["type"] == "human"):
            active_bot.pop(group_id, None)
        return

    # 找到发起任务的人类用户
    human_msgs = [m for m in context_messages if m["sender_type"] != "bot"]
    initiator_name = human_msgs[0]["sender_name"] if human_msgs else "用户"

    target_bot = mentioned_bots[0]

    # 同角色有多个 bot 时，winner 先打招呼
    same_role_bots = [b for b in all_bots if b["role"] == target_bot["role"]]
    if len(same_role_bots) > 1:
        async with get_db() as db_wow:
            wow_id = await save_message(db_wow, group_id, target_bot["id"], "wow, I got the task")
            wow_recent = await get_messages(db_wow, group_id)
        await manager.broadcast(group_id, {
            "type": "message",
            "id": wow_id,
            "member_id": target_bot["id"],
            "sender_name": target_bot["name"],
            "sender_type": "bot",
            "avatar_color": target_bot["avatar_color"],
            "content": "wow, I got the task",
            "created_at": wow_recent[-1]["created_at"] if wow_recent else "",
        })

    if "测试" in (target_bot["role"] or ""):
        handoff_prompt = (
            f"上一阶段完成，以下是交接内容：\n\n{content}\n\n"
            f"请开始测试。完成所有测试后，在最后一行写：「@{initiator_name} 测试完成，任务全部完成！」"
        )
    else:
        handoff_prompt = f"上一阶段完成，以下是交接内容：\n\n{content}\n\n请开始你的工作。"

    history, _ = build_context_message("", latest_bot_msg["sender_name"], recent)
    system_prompt = target_bot["system_prompt"] or f"你是{target_bot['name']}，{target_bot['role']}。"
    ai_reply, bot_msg_id = await stream_bot_response(group_id, target_bot, system_prompt, history, handoff_prompt)
    if not ai_reply:
        return
    asyncio.create_task(add_to_chroma(bot_msg_id, ai_reply, target_bot["role"] or "", group_id))

    active_bot[group_id] = target_bot["id"]

    await auto_continue_if_needed(group_id, target_bot, all_members)

    async with get_db() as db_r:
        bot_recent = await get_messages(db_r, group_id)
    await check_handoff(group_id, all_bots, all_members, bot_recent, _depth + 1)

async def stream_bot_response(group_id: int, bot: dict, system_prompt: str, history: list, user_msg: str):
    """流式广播 bot 回复，保存到 DB，返回 (full_text, msg_id) 或 (None, None)"""
    temp_id = str(uuid.uuid4())
    await manager.broadcast(group_id, {
        "type": "stream_start",
        "temp_id": temp_id,
        "member_id": bot["id"],
        "sender_name": bot["name"],
        "sender_type": "bot",
        "avatar_color": bot["avatar_color"],
    })
    provider = bot.get("model_provider", "deepseek")
    model = bot.get("model_name", "deepseek-chat")
    full_text = ""
    try:
        async for chunk in call_ai_stream(system_prompt, history, user_msg, provider, model):
            full_text += chunk
            await manager.broadcast(group_id, {"type": "stream_chunk", "temp_id": temp_id, "delta": chunk})
    except AIError as e:
        await manager.broadcast(group_id, {"type": "stream_error", "temp_id": temp_id, "message": str(e)})
        return None, None

    async with get_db() as db:
        msg_id = await save_message(db, group_id, bot["id"], full_text)
        recent = await get_messages(db, group_id)
    await manager.broadcast(group_id, {
        "type": "stream_end",
        "temp_id": temp_id,
        "id": msg_id,
        "member_id": bot["id"],
        "sender_name": bot["name"],
        "preview": full_text[:100],
        "created_at": recent[-1]["created_at"] if recent else "",
    })
    return full_text, msg_id

async def _send_auto_reply(group_id: int, member: dict, reply_to_id: int):
    await asyncio.sleep(1.5)
    async with get_db() as db:
        msg_id = await save_message(db, group_id, member["id"], member["auto_reply"],
                                    reply_to_id=reply_to_id, is_auto_reply=True)
        recent = await get_messages(db, group_id)
        saved = next((m for m in recent if m["id"] == msg_id), {})
    await manager.broadcast(group_id, {"type": "message", **saved})

async def _mark_read(group_id: int, member_id: int, msg_id: int):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO member_read (member_id, group_id, last_read_id) VALUES (?,?,?) "
            "ON CONFLICT(member_id, group_id) DO UPDATE SET last_read_id=excluded.last_read_id",
            (member_id, group_id, msg_id)
        )
        await db.commit()
    await manager.broadcast(group_id, {
        "type": "read",
        "member_id": member_id,
        "last_read_id": msg_id,
    })

@app.websocket("/ws/{group_id}/{member_id}")
async def websocket_endpoint(websocket: WebSocket, group_id: int, member_id: int):
    await manager.connect(websocket, group_id, member_id)
    # 告知新成员当前在线列表，并广播新成员上线
    await websocket.send_json({"type": "online_members", "member_ids": manager.get_online_member_ids(group_id)})
    await manager.broadcast(group_id, {"type": "presence", "member_id": member_id, "online": True})
    # 进入群组时，把最新消息标记为已读
    async with get_db() as db:
        async with db.execute("SELECT MAX(id) FROM messages WHERE group_id=?", (group_id,)) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            await _mark_read(group_id, member_id, row[0])
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)

            # 处理已读确认
            if payload.get("type") == "read":
                msg_id = payload.get("msg_id")
                if msg_id:
                    await _mark_read(group_id, member_id, msg_id)
                continue

            content = payload.get("content", "").strip()
            file_url = payload.get("file_url")
            file_name = payload.get("file_name")
            file_size = payload.get("file_size")
            file_type = payload.get("file_type")
            if not content and not file_url:
                continue
            reply_to_id = payload.get("reply_to_id")

            async with get_db() as db:
                sender = await get_member(db, member_id)
                if not sender:
                    continue

                msg_id = await save_message(db, group_id, member_id, content, reply_to_id,
                                            file_url, file_name, file_size, file_type)
                recent = await get_messages(db, group_id)
                saved = next((m for m in recent if m["id"] == msg_id), {})

                await manager.broadcast(group_id, {"type": "message", **saved})
                # 发送者自己立刻已读
                await _mark_read(group_id, member_id, msg_id)

                # 自动回复：@到的离线成员如果设置了自动回复则触发
                mentioned_names = set(re.findall(r'@(\S+)', content))
                if mentioned_names:
                    online_ids = set(manager.get_online_member_ids(group_id))
                    all_members_now = await get_members(db, group_id)
                    for m in all_members_now:
                        if m["name"] in mentioned_names and m["id"] not in online_ids and m.get("auto_reply"):
                            asyncio.create_task(_send_auto_reply(group_id, m, msg_id))

                all_members = await get_members(db, group_id)
                all_bots = [m for m in all_members if m["type"] == "bot"]

                # 明确 @mention 优先级最高，直接覆盖会话锁
                explicit = [b for b in all_bots if f"@{b['name']}" in content]
                if "@all" in content.lower():
                    explicit = all_bots

                if explicit:
                    triggered = explicit
                    # 更新锁：单个 @mention 锁定到该 bot，@all 不锁
                    if len(explicit) == 1:
                        active_bot[group_id] = explicit[0]["id"]
                    else:
                        active_bot.pop(group_id, None)
                else:
                    # 会话锁：如果某个 bot 正在持有会话，直接交给它
                    locked_bot_id = active_bot.get(group_id)
                    if locked_bot_id:
                        locked = next((b for b in all_bots if b["id"] == locked_bot_id), None)
                        triggered = [locked] if locked else []
                    else:
                        triggered = [b for b in all_bots if should_bot_respond(content, b["name"], b["role"] or "")]

                # 发起任务的人类用户
                human_senders = [m for m in recent if m["sender_type"] != "bot"]
                initiator_name = human_senders[0]["sender_name"] if human_senders else sender["name"]


                if triggered:
                    async def call_bot(bot):
                        history, user_msg = build_context_message(content, sender["name"], recent)
                        base_prompt = bot["system_prompt"] or f"你是{bot['name']}，{bot['role']}。"
                        # 注入角色记忆
                        memory = await get_memory_context(group_id, bot["role"] or "", content)
                        system_prompt = base_prompt + (f"\n\n{memory}" if memory else "")
                        if "测试" in (bot["role"] or ""):
                            system_prompt += f"\n\n完成测试报告后，在最后一行写：「@{initiator_name} 测试完成，任务全部完成！」"
                        ai_reply = await call_ai(system_prompt, history, user_msg)
                        return bot, ai_reply

                    # 按角色分组：同角色竞速只取第一名，不同角色并行
                    role_groups: dict[str, list] = {}
                    for bot in triggered:
                        role_groups.setdefault(bot["role"] or bot["name"], []).append(bot)

                    async def race_role_group(bots):
                        """单 bot → 流式；多 bot → 竞速取第一名"""
                        if len(bots) == 1:
                            bot = bots[0]
                            history, user_msg_text = build_context_message(content, sender["name"], recent)
                            base_prompt = bot["system_prompt"] or f"你是{bot['name']}，{bot['role']}。"
                            memory = await get_memory_context(group_id, bot["role"] or "", content)
                            system_prompt = base_prompt + (f"\n\n{memory}" if memory else "")
                            if "测试" in (bot["role"] or ""):
                                system_prompt += f"\n\n完成测试报告后，在最后一行写：「@{initiator_name} 测试完成，任务全部完成！」"
                            ai_reply, bot_msg_id = await stream_bot_response(group_id, bot, system_prompt, history, user_msg_text)
                            if not ai_reply:
                                return
                            asyncio.create_task(add_to_chroma(bot_msg_id, ai_reply, bot["role"] or "", group_id))
                            asyncio.create_task(maybe_summarize(group_id, bot["role"] or bot["name"], [bot["id"]]))
                            return

                        # 多 bot 竞速（非流式）
                        tasks = {asyncio.create_task(call_bot(b)): b for b in bots}
                        for b in bots:
                            await manager.broadcast(group_id, {
                                "type": "typing",
                                "sender_name": b["name"],
                                "avatar_color": b["avatar_color"],
                            })
                        done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
                        for t in pending:
                            t.cancel()
                        try:
                            winner_bot, ai_reply = done.pop().result()
                        except AIError as e:
                            await manager.broadcast(group_id, {"type": "error", "message": str(e)})
                            return
                        except Exception as e:
                            await manager.broadcast(group_id, {"type": "error", "message": f"未知错误：{str(e)}"})
                            return
                        async with get_db() as db_win:
                            win_msg_id = await save_message(db_win, group_id, winner_bot["id"], "wow, I got the task")
                            win_recent = await get_messages(db_win, group_id)
                        await manager.broadcast(group_id, {
                            "type": "message",
                            "id": win_msg_id,
                            "member_id": winner_bot["id"],
                            "sender_name": winner_bot["name"],
                            "sender_type": "bot",
                            "avatar_color": winner_bot["avatar_color"],
                            "content": "wow, I got the task",
                            "created_at": win_recent[-1]["created_at"] if win_recent else "",
                        })
                        async with get_db() as db2:
                            bot_msg_id = await save_message(db2, group_id, winner_bot["id"], ai_reply)
                            bot_recent = await get_messages(db2, group_id)
                        asyncio.create_task(add_to_chroma(bot_msg_id, ai_reply, winner_bot["role"] or "", group_id))
                        await manager.broadcast(group_id, {
                            "type": "message",
                            "id": bot_msg_id,
                            "member_id": winner_bot["id"],
                            "sender_name": winner_bot["name"],
                            "sender_type": "bot",
                            "avatar_color": winner_bot["avatar_color"],
                            "content": ai_reply,
                            "created_at": bot_recent[-1]["created_at"] if bot_recent else "",
                        })
                        asyncio.create_task(maybe_summarize(
                            group_id, winner_bot["role"] or winner_bot["name"], [winner_bot["id"]]
                        ))

                    # 不同角色组之间并行执行
                    await asyncio.gather(*[race_role_group(bots) for bots in role_groups.values()])

                    # 开发类角色：若未完成则自动续写
                    for bot in triggered:
                        await auto_continue_if_needed(group_id, bot, all_members)

                    # 交接检测：@mention 驱动，链式自动推进
                    await check_handoff(group_id, all_bots, all_members, recent)

                    # 更新会话锁：检查最新 bot 消息
                    async with get_db() as db3:
                        latest_msgs = await get_messages(db3, group_id, limit=3)
                    for msg in reversed(latest_msgs):
                        if msg["sender_type"] != "bot":
                            continue
                        content_latest = msg["content"]
                        # @提及了任何成员 → 锁已由 check_handoff 管理，不覆盖
                        if any(f"@{m['name']}" in content_latest for m in all_members):
                            pass
                        else:
                            # 正常对话（追问阶段）→ 锁定到这个 bot
                            active_bot[group_id] = msg["member_id"]
                        break

    except WebSocketDisconnect:
        gone_id = manager.disconnect(websocket, group_id)
        if gone_id:
            await manager.broadcast(group_id, {"type": "presence", "member_id": gone_id, "online": False})
