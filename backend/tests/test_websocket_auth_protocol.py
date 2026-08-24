import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from main import _authenticate_websocket, _websocket_protocol_auth


class _WebSocket:
    def __init__(self, protocol: str = ""):
        self.headers = {"sec-websocket-protocol": protocol}


class WebSocketAuthProtocolTests(unittest.TestCase):
    def test_extracts_jwt_and_selected_protocol(self):
        token, protocol = _websocket_protocol_auth(
            _WebSocket("chat.v1, nuke.jwt.header.payload.signature")
        )

        self.assertEqual(token, "header.payload.signature")
        self.assertEqual(protocol, "nuke.jwt.header.payload.signature")

    def test_ignores_unrelated_or_empty_auth_protocol(self):
        self.assertEqual(_websocket_protocol_auth(_WebSocket("chat.v1")), (None, None))
        self.assertEqual(_websocket_protocol_auth(_WebSocket("nuke.jwt.")), (None, "nuke.jwt."))


class WebSocketGroupAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def _db_context(self, row):
        cursor = MagicMock()
        cursor.fetchone = AsyncMock(return_value=row)
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        connection = MagicMock()
        connection.execute = MagicMock(return_value=cursor)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=connection)
        context.__aexit__ = AsyncMock(return_value=None)
        return context

    async def test_rejects_valid_token_without_group_membership(self):
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        with patch("main.auth.verify_token", return_value={"uid": 42}), \
             patch("main.db.global_db", return_value=self._db_context(None)):
            result = await _authenticate_websocket(ws, 7, 9, "token")

        self.assertIsNone(result)
        self.assertIn("membership", ws.send_json.await_args.args[0]["message"])

    async def test_allows_member_of_authorized_group(self):
        ws = MagicMock()
        with patch("main.auth.verify_token", return_value={"uid": 42}), \
             patch("main.db.global_db", return_value=self._db_context((1,))):
            result = await _authenticate_websocket(ws, 7, 9, "token")

        self.assertEqual(result, {"uid": 42})
