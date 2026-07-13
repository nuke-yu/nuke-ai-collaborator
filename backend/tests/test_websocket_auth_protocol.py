import unittest

from main import _websocket_protocol_auth


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
