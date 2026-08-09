import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from channels import ChannelProcessClient, ChannelProcessManifest
from channels.core import ChannelConversation, ChannelIdentity, DeliveryReceipt, OutboundEnvelope


class FakeStream:
    def __init__(self, response=b""):
        self.response = response
        self.writes = []

    def write(self, value):
        self.writes.append(value)

    async def drain(self):
        return None

    async def readline(self):
        return self.response


class FakeProcess:
    returncode = None

    def __init__(self, response):
        self.stdin = FakeStream()
        self.stdout = FakeStream(response)
        self.terminate = lambda: None

    async def wait(self):
        return 0


class TestChannelProcess(unittest.IsolatedAsyncioTestCase):
    async def test_only_structured_bridge_frame_crosses_process_boundary(self):
        response = {"request_id": "slack:prod:1", "receipt": {
            "channel": "slack", "idempotency_key": "event-1", "status": "sent", "external_message_id": "remote-1"
        }}
        process = FakeProcess((json.dumps(response) + "\n").encode())
        envelope = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"),
            conversation=ChannelConversation("chat-1"),
            event_type="task_stuck",
            payload={"message": "hello"},
            idempotency_key="event-1",
            group_id=7,
        )
        with patch("channels.process.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            receipt = await ChannelProcessClient(["channel-worker"], ChannelProcessManifest("slack:prod")).send(envelope)
        self.assertEqual(receipt.external_message_id, "remote-1")
        frame = json.loads(process.stdin.writes[0])
        self.assertEqual(frame["bridge"]["direction"], "outbound")
        self.assertEqual(frame["bridge"]["payload"]["outbound"]["idempotency_key"], "event-1")
        self.assertNotIn("group_event", frame)

    async def test_oversized_frame_is_rejected_before_process_start(self):
        client = ChannelProcessClient(["channel-worker"], ChannelProcessManifest("slack:prod", max_frame_bytes=100))
        envelope = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"), conversation=ChannelConversation("chat-1"),
            event_type="task_stuck", payload={"message": "x" * 1_000}, idempotency_key="event-2",
        )
        with self.assertRaises(Exception):
            await client.send(envelope)


if __name__ == "__main__":
    unittest.main()
