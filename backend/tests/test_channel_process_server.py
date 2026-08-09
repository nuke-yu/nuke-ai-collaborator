import asyncio
import json
import unittest

from channels import ChannelProcessManifest, ChannelProcessServer
from channels.core import ChannelConversation, ChannelIdentity, DeliveryReceipt, OutboundEnvelope


class _Handler:
    async def send(self, envelope):
        return DeliveryReceipt("slack", envelope.idempotency_key, "sent", "remote-1")


class _Writer:
    def __init__(self):
        self.data = []
        self.closed = False

    def write(self, data):
        self.data.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class TestChannelProcessServer(unittest.IsolatedAsyncioTestCase):
    async def test_valid_bridge_frame_is_handled_and_receipt_is_typed(self):
        manifest = ChannelProcessManifest("slack:prod")
        server = ChannelProcessServer(manifest, _Handler())
        envelope = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant"),
            conversation=ChannelConversation("chat"),
            event_type="task_stuck", payload={"message": "hello"}, idempotency_key="event-1",
        )
        bridge = {
            "direction": "outbound", "event_type": envelope.event_type,
            "idempotency_key": envelope.idempotency_key, "payload": {"outbound": envelope.to_dict()},
            "protocol_version": "channel-bridge.v1",
        }
        reader = asyncio.StreamReader()
        reader.feed_data((json.dumps({"request_id": "req-1", "manifest": {"channel_instance_id": "slack:prod", "version": "1"}, "bridge": bridge}) + "\n").encode())
        reader.feed_eof()
        writer = _Writer()
        await server.serve(reader, writer)
        response = json.loads(writer.data[0])
        self.assertEqual(response["request_id"], "req-1")
        self.assertEqual(response["receipt"]["external_message_id"], "remote-1")
        self.assertTrue(writer.closed)

    async def test_manifest_mismatch_returns_error_without_handler_call(self):
        server = ChannelProcessServer(ChannelProcessManifest("slack:prod"), _Handler())
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"request_id":"req-2","manifest":{"channel_instance_id":"other","version":"1"}}\n')
        reader.feed_eof()
        writer = _Writer()
        await server.serve(reader, writer)
        response = json.loads(writer.data[0])
        self.assertEqual(response["request_id"], "req-2")
        self.assertIn("error", response)


if __name__ == "__main__":
    unittest.main()
