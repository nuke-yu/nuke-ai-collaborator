from fastapi import WebSocket
from typing import Dict, List

class WSManager:
    def __init__(self):
        self.connections: Dict[int, List[tuple]] = {}

    def get_online_member_ids(self, group_id: int) -> list:
        if group_id not in self.connections:
            return []
        return list({mid for _, mid in self.connections[group_id]})

    async def connect(self, websocket: WebSocket, group_id: int, member_id: int):
        await websocket.accept()
        if group_id not in self.connections:
            self.connections[group_id] = []
        self.connections[group_id].append((websocket, member_id))

    def disconnect(self, websocket: WebSocket, group_id: int):
        """Remove connection. Returns member_id if they fully went offline, else None."""
        if group_id not in self.connections:
            return None
        gone_id = next((mid for ws, mid in self.connections[group_id] if ws == websocket), None)
        self.connections[group_id] = [
            (ws, mid) for ws, mid in self.connections[group_id] if ws != websocket
        ]
        still_online = any(mid == gone_id for _, mid in self.connections[group_id])
        return gone_id if gone_id and not still_online else None

    async def broadcast(self, group_id: int, message: dict):
        if group_id not in self.connections:
            return
        dead = []
        # Use a list snapshot (shallow copy) to avoid RuntimeError if connections is modified concurrently (DFT-015)
        for ws, _ in list(self.connections[group_id]):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        
        gone_ids = []
        for ws in dead:
            gone_id = self.disconnect(ws, group_id)
            if gone_id:
                gone_ids.append(gone_id)
                
        # Broadcast presence offline update for members who fully went offline (DFT-009)
        for gone_id in gone_ids:
            await self.broadcast(group_id, {"type": "presence", "member_id": gone_id, "online": False})

manager = WSManager()
