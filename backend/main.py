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
from permissions.routes import router as permissions_router
from executors import registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    """CELL-22: Supervisor lifespan. Manages central DB and Worker fleet."""
    # 1. Initialize central DB (routing/members/templates)
    tracing.setup_structured_logging()
    await db.init_central_db()
    
    # 2. Discover plugins (needed for metadata APIs like /api/plugins)
    registry.discover()
    
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
    await ai_client.aclose_client()
    await db.aclose_writer()


async def on_unread_delta(group_id: int, frame: dict):
    from db import global_db, increment_unread, get_members
    delta = frame.get("delta", 1)
    async with global_db() as db:
        # Increment for all humans in the group
        members = await get_members(db, group_id)
        for m in members:
            if m["type"] == "human":
                await increment_unread(db, group_id, m["id"], delta)


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
    def __init__(self, group_id: int, member_id: int, websocket: WebSocket):
        self.group_id = group_id
        self.member_id = member_id
        self.websocket = websocket

    async def send(self, payload: dict):
        # Supervisor calls this when it receives an upstream BROADCAST frame
        # from a worker. We fan it out to all browsers in this group.
        await manager.broadcast(self.group_id, payload)

    async def close(self):
        """Called by Supervisor when this client is evicted (H-4)."""
        # 1. Disconnect from manager
        gone_id = await manager.disconnect(self.websocket, self.group_id)
        if gone_id:
            # 2. Broadcast offline presence
            from bus.events import Presence
            import dataclasses
            presence_offline = Presence(group_id=self.group_id, member_id=gone_id, online=False)
            ev_dict = dataclasses.asdict(presence_offline)
            ev_dict["type"] = presence_offline.type
            await manager.broadcast(self.group_id, ev_dict)

@app.websocket("/ws/{group_id}/{member_id}")
async def websocket_endpoint(websocket: WebSocket, group_id: int, member_id: int, token: str = None):

    # CELL-Auth: Verify token during handshake
    user_payload = auth.verify_token(token) if token else None
    if not user_payload:
        await websocket.accept()
        await websocket.send_json({"type": "auth_error", "message": "Authentication required"})
        await websocket.close()
        return
    
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
                return

    await manager.connect(websocket, group_id, member_id)
    
    # 1. Register group proxy to supervisor for upstream fan-out
    proxy = WSClientProxy(group_id, member_id, websocket)
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

    
    try:
        while True:
            data = await websocket.receive_text()
            # Generate a new trace_id for every incoming message frame
            with tracing.trace_context(group_id=group_id):
                tid = tracing.get_trace_id()
                payload = json.loads(data)
                t = payload.get("type")

                if t == "read":
                    if msg_id := payload.get("msg_id"):
                        from core.orchestration.interaction import StandardInteraction
                        await StandardInteraction().mark_read(group_id, member_id, msg_id)
                    continue

                if t == "abort":
                    await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
                        ipc.protocol.ABORT, group_id=group_id, trace_id=tid
                    ))
                    continue

                if t == "permission_response":
                    await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
                        ipc.protocol.PERMISSION_RESPONSE, group_id=group_id, trace_id=tid, **payload
                    ))
                    continue

                # Default: User message
                await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
                    ipc.protocol.USER_MESSAGE, 
                    group_id=group_id, 
                    member_id=member_id,
                    online_ids=manager.get_online_member_ids(group_id),
                    trace_id=tid,
                    **payload
                ))

                continue

    except WebSocketDisconnect:
        gone_id = await manager.disconnect(websocket, group_id)
        if gone_id:
            presence_offline = Presence(group_id=group_id, member_id=gone_id, online=False)
            ev_dict = dataclasses.asdict(presence_offline)
            ev_dict["type"] = presence_offline.type
            await manager.broadcast(group_id, ev_dict)
        
        # If last client gone, cleanup registration and notify worker
        if not manager.get_online_member_ids(group_id):
            sup_mod.supervisor.unregister_browser(group_id, proxy)
            # Forward 'abort' to worker to cancel any group-wide pending asks/tasks
            try:
                await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
                    ipc.protocol.ABORT, group_id=group_id
                ))
            except Exception:
                pass
