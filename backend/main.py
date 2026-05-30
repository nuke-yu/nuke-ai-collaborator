import json
import asyncio
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from db import init_db, get_db, get_member, get_members, get_group, save_message, get_messages
import permissions
from ws_manager import manager
from bus import bus, ws_adapter
from bus.events import Presence, Message
from models import AddMemberRequest  # noqa: keep models importable via main
from core.orchestrator import select_triggered_bots, dispatch_bots, mark_read, send_auto_reply
from core import bg
from core import runner
from api.messages import router as message_router, UPLOAD_DIR
from api.groups import router as group_router
from api.templates import router as template_router
from api.workflow import router as workflow_router
from api.workspace import router as workspace_router
from config import read_config, write_config, _preview, FIELDS
from executors import registry
from workspace import init_all_bots, init_group_workspace
from permissions.routes import router as permissions_router
from skills.watcher import watcher
import scheduler
import sessions
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    registry.discover()
    async with get_db() as db:
        async with db.execute("SELECT DISTINCT group_id FROM members WHERE type='bot'") as cur:
            group_rows = await cur.fetchall()
        all_bots = []
        for (gid,) in group_rows:
            all_bots.extend(await get_members(db, gid))
    await init_all_bots(all_bots)
    async with get_db() as db2:
        async with db2.execute("SELECT id, name FROM groups") as cur:
            groups = await cur.fetchall()
    for gid, gname in groups:
        await init_group_workspace(gid, gname)
    watcher.start(asyncio.get_event_loop())
    adapter_task = asyncio.create_task(ws_adapter(bus))
    await scheduler.start()
    # Restore orchestrator state BEFORE recovering sessions: a recovered tool_loop_v1
    # session that was a workflow stage unit must find its workflow already loaded so
    # its completion can advance the stage (sessions/recovery.py coordination).
    await runner.resume_workflows()
    await sessions.recover_all()
    yield
    scheduler.stop()
    adapter_task.cancel()
    watcher.stop()


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
app.include_router(workspace_router)
app.include_router(permissions_router)
app.include_router(scheduler.router)


@app.get("/api/plugins")
async def list_plugins():
    return registry.all_plugins()


@app.post("/api/plugins/reload")
async def reload_plugins():
    loaded = registry.reload()
    return {"loaded": loaded}


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
    # online_members 只发给当前连接者，不走 bus
    await websocket.send_json({"type": "online_members", "member_ids": manager.get_online_member_ids(group_id)})
    await bus.publish(Presence(group_id=group_id, member_id=member_id, online=True))

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

            if payload.get("type") == "abort":
                # DFT-027: cancel the whole group's task set (dispatch + any
                # workflow-advancement tasks spawned downstream), not just one.
                bg.abort_group(group_id)
                continue

            if payload.get("type") == "permission_response":
                request_id = payload.get("request_id", "")
                approved = bool(payload.get("approved", False))
                persistence = payload.get("persistence", "once")  # "once" | "always"
                req = permissions.resolve(request_id, approved, persistence)
                if req and approved and persistence == "always":
                    bg.spawn(permissions.save_rule(
                        req.bot_id, req.tool_name, "", "allow"
                    ))
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
                await bus.publish(Message(group_id=group_id, **{k: v for k, v in saved.items() if k != "group_id"}))
                await mark_read(group_id, member_id, msg_id)

                mentioned_names = set(re.findall(r'@(\S+)', content or ""))
                if mentioned_names:
                    online_ids = set(manager.get_online_member_ids(group_id))
                    for m in await get_members(db, group_id):
                        if m["name"] in mentioned_names and m["id"] not in online_ids and m.get("auto_reply"):
                            bg.spawn(send_auto_reply(group_id, m, msg_id))

                all_members = await get_members(db, group_id)
                all_bots = [m for m in all_members if m["type"] == "bot"]
                triggered = select_triggered_bots(content or "", all_bots, group_id)
                group_info = await get_group(db, group_id) or {}

            if triggered:
                bg.spawn_group(group_id, dispatch_bots(
                    group_id, triggered, content or "", sender, recent,
                    all_bots, all_members,
                    group_name=group_info.get("name", ""),
                    group_announcement=group_info.get("announcement", ""),
                    file_url=file_url,
                    file_type=file_type,
                ))

    except WebSocketDisconnect:
        gone_id = manager.disconnect(websocket, group_id)
        if gone_id:
            await bus.publish(Presence(group_id=group_id, member_id=gone_id, online=False))
