import json
import dataclasses
import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import db
import scheduler
from ws_manager import manager

from bus.events import Presence
from runtime import tracing
from runtime import supervisor as sup_mod
from runtime import ipc
from ai import client as ai_client
from core import auth

# Shared API Routers
from api.messages import router as message_router, UPLOAD_DIR
from api.groups import router as group_router
from api.templates import router as template_router
from api.workflow import router as workflow_router
from api.workspace import router as workspace_router
from api.sessions import router as sessions_router
from api.auth import router as auth_router
from api.config import router as config_router
from permissions.routes import router as permissions_router
from executors import registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    """CELL-22: Supervisor lifespan. Manages central DB and Worker fleet."""
    # 1. Initialize central DB (routing/members/templates)
    tracing.setup_structured_logging()
    await db.init_central_db()

    # 1b. Migrate any env-provided API keys into app_config.json so the file is the
    #     single source of truth the frontend manages (config consolidation).
    from config import bootstrap_from_env
    bootstrap_from_env()

    # 2. Discover plugins (needed for metadata APIs like /api/plugins)
    registry.discover()

    # 2b. ToolRouter providers are initialized in each worker process (runtime/entry.py
    #     run_worker), NOT here.  main.py is the supervisor/API process and never
    #     executes tool loops.  Registering providers here would waste resources and
    #     leave worker-side routers empty (process-local singletons don't cross fork).

    
    # 3. Start AI client pool
    # (Worker processes will have their own AI clients)
    
    # 4. Start Supervisor Engine + Workers
    addr = ipc.make_addr("supervisor")
    num_workers = int(os.getenv("NUKE_WORKERS", "4"))
    sup = sup_mod.Supervisor(addr, num_workers=num_workers, on_unread=on_unread_delta)
    await sup.start()
    sup_mod.supervisor = sup
    
    # 5. Start Global Scheduler
    await scheduler.start()
    
    yield
    
    # Teardown
    await scheduler.stop()
    await sup.stop()
    from executors.tool_router import router as tool_router
    await tool_router.close_all()
    await ai_client.aclose_client()
    await db.aclose_writer()


async def on_unread_delta(group_id: int, payload: dict):
    """A new message was broadcast for `group_id`: +1 unread for every human who
    isn't the sender and isn't currently viewing this group. The supervisor is the
    sole writer of the central unread_counts projection (V3 §10.1)."""
    from db import global_db, write_connect, get_members, bump_unread_for_group
    sender_id = payload.get("member_id")
    online = set(manager.get_online_member_ids(group_id))
    async with global_db() as gdb:
        members = await get_members(gdb, group_id)
    if not any(m["type"] == "human" and m["id"] != sender_id and m["id"] not in online
               for m in members):
        return
    async with write_connect() as wdb:   # supervisor unbound -> central DB
        await bump_unread_for_group(wdb, group_id, members, sender_id, online)


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.include_router(message_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(group_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(template_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(workflow_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(workspace_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(sessions_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(permissions_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(config_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(scheduler.router)
app.include_router(auth_router)

# ── Metadata APIs ─────────────────────────────────────────────────────────

@app.get("/api/plugins")
async def list_plugins():
    return registry.all_plugins()

@app.get("/api/plugins/health")
async def plugins_health():
    return {"loaded": list(registry._registry.keys()), "failures": registry.failures()}

@app.post("/api/plugins/reload")
async def reload_plugins():
    loaded = registry.reload()
    return {"loaded": loaded, "failures": registry.failures()}


@app.get("/api/system/status")
async def system_status():
    """DFT-057: Aggregated metrics from Supervisor and all Workers."""
    from core import bg
    import permissions
    sup_stats = sup_mod.supervisor.get_stats() if sup_mod.supervisor else {}
    return {
        "tasks": bg.stats(),
        "websockets": manager.stats(),
        "permissions": permissions.pending_stats(),
        "supervisor": sup_stats,
    }
# ── WebSocket Shell (The Gateway) ────────────────────────────────────────

class WSClientProxy:
    """Adapts WSManager broadcast to Supervisor fan-out interface."""
    def __init__(self, group_id: int):
        self.group_id = group_id

    async def send(self, payload: dict):
        # Supervisor calls this when it receives an upstream BROADCAST frame
        # from a worker. We fan it out to all browsers in this group.
        await manager.broadcast(self.group_id, payload)

    async def close(self):
        """Called by Supervisor when this client is evicted (H-4)."""
        await manager.close_group(self.group_id)

# Exactly ONE fan-out proxy per group (the supervisor layer needs one sink per
# group; manager.broadcast already delivers to every connection in it).
_group_proxies: dict[int, WSClientProxy] = {}


async def _authenticate_websocket(websocket: WebSocket, group_id: int, member_id: int, token: str = None) -> bool:
    # CELL-Auth: Verify token during handshake
    user_payload = auth.verify_token(token) if token else None
    if not user_payload:
        await websocket.accept()
        await websocket.send_json({"type": "auth_error", "message": "Authentication required"})
        await websocket.close()
        return False
    
    # DFT-082: a valid token (a logged-in company user) is the access boundary —
    # this is a trusted, internal shared workspace, not a public multi-tenant
    # service, so we don't bind member_id to a specific user (members.user_id is
    # not populated; the old check rejected EVERY connection). We still verify the
    # member exists in this group so a client can't attach to a bogus member_id.
    async with db.global_db() as cdb:
        async with cdb.execute("SELECT 1 FROM members WHERE id = ? AND group_id = ?", (member_id, group_id)) as cur:
            if not await cur.fetchone():
                await websocket.accept()
                await websocket.send_json({"type": "auth_error", "message": "Unknown member for this group"})
                await websocket.close()
                return False
    return True


async def _initialize_websocket_session(websocket: WebSocket, group_id: int, member_id: int):
    await manager.connect(websocket, group_id, member_id)
    
    # 1. Reuse the group's single fan-out proxy (register it only for the first
    #    connection). One proxy per *connection* would fan every worker event out
    #    once per connection -> the bot's reply rendered N times.
    proxy = _group_proxies.get(group_id)
    if proxy is None:
        proxy = WSClientProxy(group_id)
        _group_proxies[group_id] = proxy
        sup_mod.supervisor.register_browser(group_id, proxy)
    
    # 2. Synchronize initial UI state
    await websocket.send_json({
        "type": "online_members", 
        "member_ids": manager.get_online_member_ids(group_id)
    })
    # Presence is a global concern (broadcast to all)
    presence_ev = Presence(group_id=group_id, member_id=member_id, online=True)
    ev_dict = dataclasses.asdict(presence_ev)
    ev_dict["type"] = presence_ev.type
    await manager.broadcast(group_id, ev_dict)


async def _handle_incoming_message(payload: dict, group_id: int, member_id: int, trace_id: str):
    t = payload.get("type")

    if t == "read":
        if msg_id := payload.get("msg_id"):
            from core.orchestration.interaction import StandardInteraction
            await StandardInteraction().mark_read(group_id, member_id, msg_id)
        # reading the group clears its unread badge (central projection)
        from db import write_connect, reset_unread
        async with write_connect() as udb:
            await reset_unread(udb, group_id, member_id)
        return

    if t == "abort":
        await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
            ipc.protocol.ABORT, group_id=group_id, trace_id=trace_id
        ))
        return

    if t == "confirm":
        await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
            ipc.protocol.CONFIRM, group_id=group_id, trace_id=trace_id,
            gate_id=payload.get("gate_id")
        ))
        return

    if t == "start_workflow":
        await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
            ipc.protocol.START_WORKFLOW, group_id=group_id, trace_id=trace_id
        ))
        return

    if t in ("query", "mutate"):
        # supervisor only routes; member_id comes from the authed URL,
        # not the client payload. Strip reserved envelope keys.
        fields = {k: v for k, v in payload.items()
                  if k not in ("type", "group_id", "trace_id", "member_id")}
        mtype = ipc.protocol.QUERY if t == "query" else ipc.protocol.MUTATE
        await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
            mtype, group_id=group_id, trace_id=trace_id, member_id=member_id, **fields
        ))
        return

    if t == "permission_response":
        await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
            ipc.protocol.PERMISSION_RESPONSE, group_id=group_id, trace_id=trace_id, **payload
        ))
        return

    # Default: User message. Strip reserved envelope keys from the
    # client payload (same as the query/mutate branch above) — else a
    # client-sent group_id/type/trace_id/member_id collides with the
    # envelope's own kwargs and raises "multiple values for ...".
    fields = {k: v for k, v in payload.items()
              if k not in ("type", "group_id", "trace_id", "member_id")}
    await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
        ipc.protocol.USER_MESSAGE,
        group_id=group_id,
        member_id=member_id,
        online_ids=manager.get_online_member_ids(group_id),
        trace_id=trace_id,
        **fields
    ))


async def _handle_websocket_disconnect(websocket: WebSocket, group_id: int):
    gone_id = await manager.disconnect(websocket, group_id)
    if gone_id:
        presence_offline = Presence(group_id=group_id, member_id=gone_id, online=False)
        ev_dict = dataclasses.asdict(presence_offline)
        ev_dict["type"] = presence_offline.type
        await manager.broadcast(group_id, ev_dict)

    # Last connection left → drop the group's single fan-out proxy and abort
    # any group-wide pending tasks. (While other connections remain, the one
    # proxy keeps serving them, so we don't touch it per-disconnect.)
    if not manager.get_online_member_ids(group_id):
        p = _group_proxies.pop(group_id, None)
        if p:
            sup_mod.supervisor.unregister_browser(group_id, p)
        try:
            await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
                ipc.protocol.ABORT, group_id=group_id
            ))
        except Exception:
            pass


@app.websocket("/ws/{group_id}/{member_id}")
async def websocket_endpoint(websocket: WebSocket, group_id: int, member_id: int, token: str = None):
    if not await _authenticate_websocket(websocket, group_id, member_id, token):
        return

    await _initialize_websocket_session(websocket, group_id, member_id)
    
    try:
        while True:
            data = await websocket.receive_text()
            # Generate a new trace_id for every incoming message frame
            with tracing.trace_context(group_id=group_id):
                tid = tracing.get_trace_id()
                payload = json.loads(data)
                await _handle_incoming_message(payload, group_id, member_id, tid)
    except WebSocketDisconnect:
        await _handle_websocket_disconnect(websocket, group_id)
