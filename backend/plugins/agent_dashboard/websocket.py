"""
plugins/agent_dashboard/websocket.py — Dashboard WebSocket Endpoint

Provides a dedicated WebSocket channel for the dashboard frontend.
Clients connect and receive real-time progress updates for coding agent tasks.

Endpoints:
  /ws/agent/{group_id}  — Subscribe to progress updates for a specific task
  /ws/agent/all         — Subscribe to progress updates for all active tasks

Message format (server → client):
  {
    "type": "agent_progress",
    "group_id": 1,
    "task_id": "task_123",
    "phase": "coding",
    "percent": 45,
    "detail": "编辑 src/api.py",
    "status": "running",
    "iteration": 3,
    "elapsed_sec": 120.5,
    ...
  }
"""
import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

router = APIRouter()

# Module-level reference to the ProgressAdapter (set by __init__.register)
_adapter = None


def set_adapter(adapter):
    """Called by plugin __init__ to inject the ProgressAdapter instance."""
    global _adapter
    _adapter = adapter


class DashboardConnection:
    """Manages a single dashboard WebSocket client connection."""

    def __init__(self, ws: WebSocket, group_filter: int | None = None):
        self.ws = ws
        self.group_filter = group_filter  # None = all groups
        self._closed = False

    async def send(self, data: dict) -> bool:
        """Send a progress update to the client. Returns False if send fails."""
        if self._closed:
            return False
        try:
            await self.ws.send_json(data)
            return True
        except Exception:
            self._closed = True
            return False


# Active dashboard connections
_connections: dict[int, set[DashboardConnection]] = {}  # group_id → {connections}
_all_connections: set[DashboardConnection] = set()       # subscribed to all


@router.websocket("/ws/agent/{group_id}")
async def dashboard_ws(ws: WebSocket, group_id: int):
    """WebSocket endpoint for a specific task's progress."""
    await ws.accept()
    conn = DashboardConnection(ws, group_filter=group_id)

    # Register connection
    _connections.setdefault(group_id, set()).add(conn)

    # Send current state immediately
    if _adapter:
        current = _adapter.get_progress(group_id)
        if current:
            await conn.send(current)

    try:
        # Keep connection alive, handle client messages (e.g. ping/pong)
        while True:
            try:
                data = await ws.receive_text()
                # Client can send commands (future: steer, etc.)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                pass
    finally:
        _connections.get(group_id, set()).discard(conn)
        if not _connections.get(group_id):
            _connections.pop(group_id, None)


@router.websocket("/ws/agent/all")
async def dashboard_all_ws(ws: WebSocket):
    """WebSocket endpoint for all active tasks' progress."""
    await ws.accept()
    conn = DashboardConnection(ws, group_filter=None)
    _all_connections.add(conn)

    # Send current state of all active tasks
    if _adapter:
        for state in _adapter.get_all_active():
            await conn.send(state)

    try:
        while True:
            try:
                data = await ws.receive_text()
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                pass
    finally:
        _all_connections.discard(conn)


async def push_to_clients(update: dict):
    """Push a progress update to relevant dashboard WebSocket clients."""
    group_id = update.get("group_id")

    # Send to group-specific connections
    for conn in list(_connections.get(group_id, ())):
        if not await conn.send(update):
            _connections.get(group_id, set()).discard(conn)

    # Send to "all" subscribers
    for conn in list(_all_connections):
        if not await conn.send(update):
            _all_connections.discard(conn)


async def consumer_loop(adapter):
    """Background task: consume progress updates from adapter queue and push to WS clients.

    This runs as a plugin background task, reading from the adapter's update_queue
    and distributing updates to connected dashboard WebSocket clients.
    """
    queue = adapter.update_queue
    log.info("agent_dashboard: consumer loop started")

    while True:
        try:
            update = await queue.get()
            await push_to_clients(update)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("agent_dashboard: consumer loop error")
