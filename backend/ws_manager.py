import asyncio
import logging
from core import config
from fastapi import WebSocket
from typing import Dict, List

# DFT-030: cap how long a single client's send_json may block the shared
# broadcast loop. A half-open TCP socket makes send_json await indefinitely,
# stalling event delivery for the whole app (the bus adapter fans out through
# here serially). On timeout we treat the client as dead and disconnect it.
_SEND_TIMEOUT = config.WS_SEND_TIMEOUT

log = logging.getLogger(__name__)


class WSManager:
    def __init__(self):
        self.connections: Dict[int, List[tuple]] = {}
        # Point 5: Concurrency Defense (DFT-047)
        self._lock = asyncio.Lock()

    def stats(self) -> dict:
        """运行指标快照（DFT-057）：在线群数 / 总连接数 / 按群连接数。"""
        return {
            "groups_online": len(self.connections),
            "total_connections": sum(len(c) for c in self.connections.values()),
            "connections_by_group": {gid: len(c) for gid, c in self.connections.items()},
        }

    def get_online_member_ids(self, group_id: int) -> list:
        if group_id not in self.connections:
            return []
        return list({mid for _, mid in self.connections[group_id]})

    async def connect(
        self, websocket: WebSocket, group_id: int, member_id: int,
        *, subprotocol: str | None = None,
    ):
        if subprotocol:
            await websocket.accept(subprotocol=subprotocol)
        else:
            await websocket.accept()
        async with self._lock:
            if group_id not in self.connections:
                self.connections[group_id] = []
            self.connections[group_id].append((websocket, member_id))

    async def disconnect(self, websocket: WebSocket, group_id: int):
        """Remove connection. Returns member_id if they fully went offline, else None."""
        async with self._lock:
            return self._disconnect_sync(websocket, group_id)

    async def close_group(self, group_id: int):
        """Close and disconnect all websockets for a group."""
        async with self._lock:
            if group_id not in self.connections:
                return
            conns = list(self.connections[group_id])
        
        # Close all websockets
        for ws, _ in conns:
            try:
                await ws.close()
            except Exception:
                log.exception("ws_manager: failed to close websocket for group %s", group_id)
        
        # Disconnect them all and broadcast offline presence
        gone_ids = []
        async with self._lock:
            if group_id in self.connections:
                for ws, mid in list(self.connections[group_id]):
                    gone_id = self._disconnect_sync(ws, group_id)
                    if gone_id:
                        gone_ids.append(gone_id)
                        
        for gone_id in gone_ids:
            await self.broadcast(group_id, {"type": "presence", "member_id": gone_id, "online": False})

    def _disconnect_sync(self, websocket: WebSocket, group_id: int) -> int | None:
        """Internal synchronous disconnect logic to be called under lock."""
        if group_id not in self.connections:
            return None
        gone_id = next((mid for ws, mid in self.connections[group_id] if ws == websocket), None)
        self.connections[group_id] = [
            (ws, mid) for ws, mid in self.connections[group_id] if ws != websocket
        ]
        if not self.connections[group_id]:
            self.connections.pop(group_id)

        still_online = any(mid == gone_id for _, mid in self.connections.get(group_id, []))
        return gone_id if gone_id and not still_online else None

    async def broadcast(self, group_id: int, message: dict):
        # Prevent re-entrant calls from same-coroutine recursion (Point 5 & DFT-047)
        dead = []
        async with self._lock:
            if group_id not in self.connections:
                return
            # Take a snapshot under lock to iterate safely
            current_group_conns = list(self.connections[group_id])

        async def _send_one(ws):
            try:
                await asyncio.wait_for(ws.send_json(message), _SEND_TIMEOUT)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*[_send_one(ws) for ws, _ in current_group_conns], return_exceptions=True)
        dead = [r for r in results if r is not None and not isinstance(r, Exception)]
        
        if not dead:
            return

        gone_ids = []
        async with self._lock:
            for ws in dead:
                gone_id = self._disconnect_sync(ws, group_id)
                if gone_id:
                    gone_ids.append(gone_id)
                
        # Broadcast presence offline update for members who fully went offline (DFT-009)
        # We do this OUTSIDE the lock to avoid deadlock and recursively.
        for gone_id in gone_ids:
            # Note: We call broadcast again. Since we released the lock, this is safe.
            await self.broadcast(group_id, {"type": "presence", "member_id": gone_id, "online": False})

manager = WSManager()
