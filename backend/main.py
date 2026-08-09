import json
import dataclasses
import asyncio
import os
import logging
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Import feature flags early to initialize them
from utils import feature_flags

import db
import scheduler
from ws_manager import manager

from bus.events import Presence
from runtime import tracing
from runtime import supervisor as sup_mod
from runtime import ipc
from ai import client as ai_client
from core import auth
from core import config as core_config

# Shared API Routers
from api.messages import router as message_router, UPLOAD_DIR
from api.groups import router as group_router
from api.templates import router as template_router
from api.workflow import router as workflow_router
from api.workspace import router as workspace_router, preview_router as workspace_preview_router
from api.sessions import router as sessions_router
from api.auth import router as auth_router
from api.config import router as config_router
from api.media import router as media_router
from permissions.routes import router as permissions_router
from api.skills import router as skills_router
from api.personal_memory import router as personal_memory_router
from api.artifacts import router as artifacts_router
from executors import registry
from api.admin_deps import require_operator, audit_control_plane

async def _media_reaper_loop():
    """Purge old MCP screenshots + orphaned staging files every 6h. User uploads untouched."""
    from core import media
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            removed = await asyncio.to_thread(media.reap_screenshots)
            if removed:
                logging.getLogger("main").info("media reaper removed %d screenshot/staging files", removed)
        except asyncio.CancelledError:
            break
        except Exception:
            logging.getLogger("main").exception("media reaper iteration failed")


async def _cancel_and_wait(task: asyncio.Task | None) -> None:
    """Cancel a background task and wait for it to finish shutting down."""
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _clear_supervisor_ref(sup) -> None:
    """Drop the global supervisor reference if it still points at `sup`."""
    if sup_mod.supervisor is sup:
        sup_mod.supervisor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """CELL-22: Supervisor lifespan. Manages central DB and Worker fleet."""
    core_config.validate_runtime_security()

    # 1. Initialize feature flags
    feature_flags.initialize_feature_flags()

    # 2. Initialize central DB (routing/members/templates)
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
    num_workers = int(os.getenv("NUKE_WORKERS", "8"))
    sup = sup_mod.Supervisor(addr, num_workers=num_workers, on_unread=on_unread_delta)
    await sup.start()
    sup_mod.supervisor = sup
    
    # 5. Start Global Scheduler
    await scheduler.start()

    # 6. Load plugins (auto-discovery from NUKE_PLUGINS_DIR or plugins/ directory)
    from plugins.host import PluginHost
    plugin_host = PluginHost(app, sup)
    await plugin_host.discover_and_load()

    # 7. Periodic media reaper (purges old MCP screenshots + orphaned staging; never uploads)
    media_reaper_task = asyncio.create_task(_media_reaper_loop())

    yield

    # Teardown
    await plugin_host.unload_all()
    await _cancel_and_wait(media_reaper_task)
    scheduler.stop()   # sync (returns None); awaiting it raised TypeError on teardown
    await sup.stop()
    _clear_supervisor_ref(sup)
    from executors.tool_router import router as tool_router
    await tool_router.close_all()
    await ai_client.aclose_client()
    from ai import embeddings as _embeddings
    _embeddings.close_embedding_client()   # DFT-035: close sync embeddings client
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
# Preview self-authenticates via the JWT in its URL path (no header), so it must
# NOT carry the router-level get_current_user dependency.
app.include_router(workspace_preview_router)
# Media self-authenticates via the HMAC signature in its query string (an <img>
# tag can't send a Bearer header), so likewise NO get_current_user dependency.
app.include_router(media_router)
app.include_router(sessions_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(permissions_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(skills_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(config_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(artifacts_router, dependencies=[Depends(auth.get_current_user)])
app.include_router(personal_memory_router)
app.include_router(scheduler.router)
app.include_router(auth_router)

# ── Metadata APIs ─────────────────────────────────────────────────────────

@app.get("/api/plugins")
async def list_plugins(request: Request, user=Depends(require_operator)):
    audit_control_plane("plugins.list", user, request)
    return registry.all_plugins()

@app.get("/api/plugins/health")
async def plugins_health(request: Request, user=Depends(require_operator)):
    audit_control_plane("plugins.health", user, request)
    return {"loaded": list(registry._registry.keys()), "failures": registry.failures()}

@app.post("/api/plugins/reload")
async def reload_plugins(request: Request, user=Depends(require_operator)):
    loaded = registry.reload()
    audit_control_plane("plugins.reload", user, request, loaded=loaded)
    return {"loaded": loaded, "failures": registry.failures()}


@app.get("/mcp/oauth/callback")
async def mcp_oauth_callback(code: str = "", state: str = ""):
    """OAuth redirect target for MCP servers. Relays the authorization code to the
    mcp-collector over the bus (it owns the in-flight flow, keyed by state)."""
    from fastapi.responses import HTMLResponse
    if sup_mod.supervisor and code and state:
        await sup_mod.supervisor.send_to_worker_id(
            ipc.protocol.MCP_COLLECTOR_ID,
            ipc.protocol.envelope(
                ipc.protocol.MCP_OAUTH_CALLBACK, group_id=0, code=code, state=state),
        )
        return HTMLResponse("<h3>✅ 授权完成，可以关闭此页面，回到对话即可。</h3>")
    return HTMLResponse("<h3>授权回调缺少 code/state 参数。</h3>", status_code=400)


@app.get("/health/liveness")
async def liveness():
    """Verify that the supervisor process is running."""
    return {"status": "ok"}


@app.get("/health/readiness")
async def readiness():
    """Verify that the global DB is writable and workers are connected."""
    from fastapi import HTTPException
    import logging
    import time
    from db import global_db
    from runtime import supervisor as sup_mod

    # 1. Verify Global Database Writability (moved from liveness)
    try:
        async with global_db() as db:
            await db.execute("CREATE TABLE IF NOT EXISTS health_check (id INTEGER PRIMARY KEY, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            await db.execute("INSERT OR REPLACE INTO health_check (id) VALUES (1)")
            await db.commit()
    except Exception as e:
        logging.exception("Readiness check failed: DB write failed")
        raise HTTPException(status_code=503, detail=f"Database not writable: {e}")

    # 2. Verify Supervisor and Worker fleet status
    sup = sup_mod.supervisor
    if sup is None:
        raise HTTPException(status_code=503, detail="Supervisor not initialized")
        
    if sup._num_workers > 0:
        # Check mcp-collector is connected
        from runtime.ipc.protocol import MCP_COLLECTOR_ID
        if MCP_COLLECTOR_ID not in sup._workers:
            raise HTTPException(status_code=503, detail="mcp-collector is not connected")
            
        # Check that all expected workers are connected and have fresh heartbeats
        for i in range(sup._num_workers):
            wid = f"w{i}"
            if wid not in sup._workers:
                raise HTTPException(status_code=503, detail=f"Worker {wid} is not connected")
                
            ts = sup._worker_stats_ts.get(wid)
            if ts is None:
                raise HTTPException(status_code=503, detail=f"Worker {wid} heartbeat not yet received")
                
            age = time.time() - ts
            if age > 45.0:
                raise HTTPException(status_code=503, detail=f"Worker {wid} heartbeat is too old ({age:.1f}s)")
                
    return {
        "status": "ready",
        "connected_workers": list(sup._workers.keys()),
    }


@app.get("/api/system/status")
async def system_status(request: Request, user=Depends(require_operator)):
    """DFT-057: Aggregated metrics from Supervisor and all Workers."""
    from core import bg
    import permissions
    sup_stats = sup_mod.supervisor.get_stats() if sup_mod.supervisor else {}
    audit_control_plane("system.status", user, request)
    return {
        "tasks": bg.stats(),
        "websockets": manager.stats(),
        "permissions": permissions.pending_stats(),
        "supervisor": sup_stats,
    }
@app.get("/metrics")
async def metrics(request: Request):
    """DFT-032: Prometheus exposition for the Supervisor process fleet.

    Single scrape target — the Supervisor is the sole aggregator of fleet state,
    and workers are IPC-only subprocesses with no HTTP server of their own.
    """
    from core import config as _cfg
    from runtime import metrics as _metrics
    if not _cfg.METRICS_ENABLED:
        return Response(status_code=404)
    if _cfg.METRICS_TOKEN:
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {_cfg.METRICS_TOKEN}":
            return Response(status_code=401)
    if sup_mod.supervisor is None:
        return Response(status_code=503)
    body, content_type = _metrics.render_metrics(sup_mod.supervisor)
    return Response(content=body, media_type=content_type)


# ── WebSocket Shell (The Gateway) ────────────────────────────────────────

class WSClientProxy:
    """Adapts WSManager broadcast to Supervisor fan-out interface."""
    def __init__(self, group_id: int):
        self.group_id = group_id

    async def send(self, payload: dict):
        # Supervisor calls this when it receives an upstream BROADCAST frame
        # from a worker. We fan it out to all browsers in this group.
        from runtime.event_envelope import make_event_envelope
        await manager.broadcast(
            self.group_id,
            make_event_envelope(payload, group_id=self.group_id),
        )

    async def close(self):
        """Called by Supervisor when this client is evicted (H-4)."""
        await manager.close_group(self.group_id)

# Exactly ONE fan-out proxy per group (the supervisor layer needs one sink per
# group; manager.broadcast already delivers to every connection in it).
_group_proxies: dict[int, WSClientProxy] = {}


_WS_AUTH_PROTOCOL_PREFIX = "nuke.jwt."


def _websocket_protocol_auth(websocket: WebSocket) -> tuple[str | None, str | None]:
    """Extract JWT from the negotiated protocol header without putting it in the URL."""
    requested = websocket.headers.get("sec-websocket-protocol", "")
    for value in (item.strip() for item in requested.split(",")):
        if value.startswith(_WS_AUTH_PROTOCOL_PREFIX):
            token = value[len(_WS_AUTH_PROTOCOL_PREFIX):]
            return (token or None), value
    return None, None


async def _authenticate_websocket(
    websocket: WebSocket, group_id: int, member_id: int, token: str = None,
    *, subprotocol: str | None = None,
) -> dict | None:
    # CELL-Auth: Verify token during handshake
    user_payload = auth.verify_token(token) if token else None
    if not user_payload:
        await websocket.accept(subprotocol=subprotocol)
        await websocket.send_json({"type": "auth_error", "message": "Authentication required"})
        await websocket.close()
        return None
    
    # DFT-082: a valid token (a logged-in company user) is the access boundary —
    # this is a trusted, internal shared workspace, not a public multi-tenant
    # service, so we don't bind member_id to a specific user (members.user_id is
    # not populated; the old check rejected EVERY connection). We still verify the
    # member exists in this group so a client can't attach to a bogus member_id.
    async with db.global_db() as cdb:
        async with cdb.execute("SELECT 1 FROM members WHERE id = ? AND group_id = ?", (member_id, group_id)) as cur:
            if not await cur.fetchone():
                await websocket.accept(subprotocol=subprotocol)
                await websocket.send_json({"type": "auth_error", "message": "Unknown member for this group"})
                await websocket.close()
                return None
    return user_payload


async def _initialize_websocket_session(
    websocket: WebSocket, group_id: int, member_id: int,
    *, subprotocol: str | None = None,
):
    await manager.connect(websocket, group_id, member_id, subprotocol=subprotocol)
    
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


async def _handle_incoming_message(payload: dict, group_id: int, member_id: int, trace_id: str,
                                   user_id: int = 0):
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
            ipc.protocol.ABORT, group_id=group_id, trace_id=trace_id,
            lang=payload.get("lang")
        ))
        return

    if t == "confirm":
        await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
            ipc.protocol.CONFIRM, group_id=group_id, trace_id=trace_id,
            gate_id=payload.get("gate_id"),
            revise=bool(payload.get("revise", False)),
            note=(payload.get("note") or ""),
            lang=payload.get("lang")
        ))
        return

    if t == "start_workflow":
        await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
            ipc.protocol.START_WORKFLOW, group_id=group_id, trace_id=trace_id,
            lang=payload.get("lang")
        ))
        return

    if t in ("query", "mutate"):
        # supervisor only routes; member_id comes from the authed URL,
        # not the client payload. Strip reserved envelope keys.
        fields = {k: v for k, v in payload.items()
                  if k not in ("type", "group_id", "trace_id", "member_id", "user_id")}
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
              if k not in ("type", "group_id", "trace_id", "member_id", "user_id")}
    await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
        ipc.protocol.USER_MESSAGE,
        group_id=group_id,
        member_id=member_id,
        user_id=user_id,
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


@app.websocket("/ws/{group_id:int}/{member_id:int}")
async def websocket_endpoint(websocket: WebSocket, group_id: int, member_id: int, token: str = None):
    protocol_token, subprotocol = _websocket_protocol_auth(websocket)
    # Query token is a temporary compatibility fallback for older clients.
    auth_token = protocol_token or token
    user_payload = await _authenticate_websocket(
        websocket, group_id, member_id, auth_token, subprotocol=subprotocol,
    )
    if not user_payload:
        return

    await _initialize_websocket_session(
        websocket, group_id, member_id, subprotocol=subprotocol,
    )
    
    try:
        while True:
            data = await websocket.receive_text()
            # Generate a new trace_id for every incoming message frame
            with tracing.trace_context(group_id=group_id):
                tid = tracing.get_trace_id()
                payload = json.loads(data)
                await _handle_incoming_message(payload, group_id, member_id, tid, int(user_payload["uid"]))
    except WebSocketDisconnect:
        await _handle_websocket_disconnect(websocket, group_id)


# --- Single-container deploy: serve the built frontend SPA if present ---------
# No-op unless NUKE_FRONTEND_DIST points at a built frontend dir (frontend/dist),
# so dev (Vite serving the frontend separately) is unaffected. Registered LAST,
# after every API / WS / health / metrics route, so it never shadows them.
_FRONTEND_DIST = os.environ.get("NUKE_FRONTEND_DIST")
if _FRONTEND_DIST and os.path.isdir(_FRONTEND_DIST):
    from pathlib import Path as _Path
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    _DIST = _Path(_FRONTEND_DIST).resolve()
    _NON_SPA_PREFIXES = ("api/", "ws/", "health", "metrics", "uploads", "media/", "mcp/")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_spa(full_path: str):
        # Unknown API-ish paths 404 (don't masquerade as the SPA shell)
        if full_path.startswith(_NON_SPA_PREFIXES):
            raise HTTPException(status_code=404)
        # Serve a real built asset if it exists (guard against path traversal),
        # otherwise fall back to index.html so client-side routing / deep links work.
        if full_path:
            target = (_DIST / full_path).resolve()
            if target.is_file() and (target == _DIST or _DIST in target.parents):
                return FileResponse(str(target))
        return FileResponse(str(_DIST / "index.html"))


if __name__ == "__main__":
    import sys
    import uvicorn

    port = 8000
    if "--port" in sys.argv:
        try:
            idx = sys.argv.index("--port")
            if idx + 1 < len(sys.argv):
                port = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass

    print(f"[Backend] Starting FastAPI Uvicorn server on http://127.0.0.1:{port}")
    uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="info")
