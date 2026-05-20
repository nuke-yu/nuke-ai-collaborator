import json
import asyncio
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import init_db, get_db, get_member, get_members, save_message, get_messages
from ws_manager import manager
from models import AddMemberRequest  # noqa: keep models importable via main
from bot_orchestrator import select_triggered_bots, dispatch_bots, mark_read, send_auto_reply
from message_routes import router as message_router, UPLOAD_DIR
from group_routes import router as group_router
from template_routes import router as template_router
from workflow_routes import router as workflow_router
from config import read_config, write_config, _preview, FIELDS
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.include_router(message_router)
app.include_router(group_router)
app.include_router(template_router)
app.include_router(workflow_router)


@app.get("/api/config")
async def get_config():
    cfg = read_config()
    result = {}
    for field, env_var in FIELDS.items():
        val = cfg.get(field, "").strip() or os.getenv(env_var, "")
        result[field] = {"configured": bool(val), "preview": _preview(val)}
    return result


@app.put("/api/config")
async def save_config(data: dict):
    cfg = read_config()
    allowed = {"deepseek_api_key", "openai_api_key", "anthropic_api_key", "ollama_base_url"}
    for key, val in data.items():
        if key in allowed:
            cfg[key] = val.strip()
    write_config(cfg)
    return {"ok": True}


@app.websocket("/ws/{group_id}/{member_id}")
async def websocket_endpoint(websocket: WebSocket, group_id: int, member_id: int):
    await manager.connect(websocket, group_id, member_id)
    await websocket.send_json({"type": "online_members", "member_ids": manager.get_online_member_ids(group_id)})
    await manager.broadcast(group_id, {"type": "presence", "member_id": member_id, "online": True})

    async with get_db() as db:
        async with db.execute("SELECT MAX(id) FROM messages WHERE group_id=?", (group_id,)) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            await mark_read(group_id, member_id, row[0])

    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)

            if payload.get("type") == "read":
                if msg_id := payload.get("msg_id"):
                    await mark_read(group_id, member_id, msg_id)
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
                await mark_read(group_id, member_id, msg_id)

                mentioned_names = set(re.findall(r'@(\S+)', content or ""))
                if mentioned_names:
                    online_ids = set(manager.get_online_member_ids(group_id))
                    for m in await get_members(db, group_id):
                        if m["name"] in mentioned_names and m["id"] not in online_ids and m.get("auto_reply"):
                            asyncio.create_task(send_auto_reply(group_id, m, msg_id))

                all_members = await get_members(db, group_id)
                all_bots = [m for m in all_members if m["type"] == "bot"]
                triggered = select_triggered_bots(content or "", all_bots, group_id)

            if triggered:
                await dispatch_bots(group_id, triggered, content or "", sender, recent, all_bots, all_members)

    except WebSocketDisconnect:
        gone_id = manager.disconnect(websocket, group_id)
        if gone_id:
            await manager.broadcast(group_id, {"type": "presence", "member_id": gone_id, "online": False})
