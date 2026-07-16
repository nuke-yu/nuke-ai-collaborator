"""WebSocket routing tests for the agent dashboard."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from plugins.agent_dashboard import websocket


class TestAgentWebSocketRoutes(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(websocket.router)
        websocket._connections.clear()
        websocket._all_connections.clear()

    def tearDown(self):
        websocket.set_adapter(None)
        websocket._connections.clear()
        websocket._all_connections.clear()

    def test_all_route_completes_real_websocket_handshake(self):
        adapter = MagicMock()
        adapter.get_all_active.return_value = [
            {"type": "agent_progress", "group_id": 7, "status": "running"}
        ]
        websocket.set_adapter(adapter)

        with patch.object(
            websocket, "_authenticate_ws", new=AsyncMock(return_value=(True, None))
        ):
            with TestClient(self.app) as client:
                with client.websocket_connect("/ws/agent/all") as ws:
                    self.assertEqual(ws.receive_json()["group_id"], 7)
                    ws.send_json({"type": "ping"})
                    self.assertEqual(ws.receive_json(), {"type": "pong"})

        adapter.get_all_active.assert_called_once_with()
        adapter.get_progress.assert_not_called()

    def test_numeric_group_route_still_targets_one_group(self):
        adapter = MagicMock()
        adapter.get_progress.return_value = {
            "type": "agent_progress",
            "group_id": 7,
            "status": "running",
        }
        websocket.set_adapter(adapter)

        with patch.object(
            websocket, "_authenticate_ws", new=AsyncMock(return_value=(True, None))
        ):
            with TestClient(self.app) as client:
                with client.websocket_connect("/ws/agent/7") as ws:
                    self.assertEqual(ws.receive_json()["group_id"], 7)

        adapter.get_progress.assert_called_once_with(7)

    def test_dashboard_route_is_not_shadowed_by_numeric_chat_route(self):
        app = FastAPI()

        @app.websocket("/ws/{group_id:int}/{member_id:int}")
        async def chat_ws(ws: WebSocket, group_id: int, member_id: int):
            raise AssertionError("dashboard path must not hit chat websocket")

        app.include_router(websocket.router)
        websocket.set_adapter(MagicMock())
        websocket._adapter.get_all_active.return_value = []

        with patch.object(
            websocket, "_authenticate_ws", new=AsyncMock(return_value=(True, None))
        ):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/agent/all") as ws:
                    ws.send_json({"type": "ping"})
                    self.assertEqual(ws.receive_json(), {"type": "pong"})
