import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from channels import ChannelProcessClient, ChannelProcessError, ChannelProcessManifest
from channels.core import ChannelConversation, ChannelIdentity, DeliveryReceipt, OutboundEnvelope
from channels.secrets import EnvironmentSecretResolver


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
        self.terminate_calls = 0
        self.kill_calls = 0

        def terminate():
            self.terminate_calls += 1

        def kill():
            self.kill_calls += 1

        self.terminate = terminate
        self.kill = kill

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
        with self.assertRaises(ChannelProcessError):
            await client.send(envelope)

    async def test_timeout_stops_old_process_before_next_request(self):
        process = FakeProcess(b"")
        process.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
        client = ChannelProcessClient(["channel-worker"], ChannelProcessManifest("slack:prod", max_seconds=0.01))
        envelope = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"), conversation=ChannelConversation("chat-1"),
            event_type="task_stuck", payload={}, idempotency_key="event-timeout",
        )
        with patch("channels.process.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as start:
            with self.assertRaises(ChannelProcessError):
                await client.send(envelope)
        self.assertEqual(process.terminate_calls, 1)
        start.assert_awaited_once()
        self.assertEqual(start.await_args.kwargs["limit"], 256_000)

    async def test_close_kills_process_when_terminate_does_not_finish(self):
        process = FakeProcess(b"")
        process.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        client = ChannelProcessClient(["channel-worker"], ChannelProcessManifest("slack:prod"))
        client._process = process
        await client.close()
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)

    async def test_child_receives_minimal_explicit_environment(self):
        response = {"request_id": "slack:prod:1", "receipt": {
            "channel": "slack", "idempotency_key": "event-env", "status": "sent", "external_message_id": "remote-1"
        }}
        process = FakeProcess((json.dumps(response) + "\n").encode())
        envelope = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"), conversation=ChannelConversation("chat-1"),
            event_type="task_stuck", payload={}, idempotency_key="event-env",
        )
        resolver = EnvironmentSecretResolver({"SLACK_TOKEN": "secret", "HOME": "/must-not-leak"})
        with patch("channels.process.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as start:
            await ChannelProcessClient(
                ["channel-worker"], ChannelProcessManifest("slack:prod", env_keys=("SLACK_TOKEN",)),
                secret_resolver=resolver,
            ).send(envelope)
        env = start.await_args.kwargs["env"]
        self.assertEqual(env["SLACK_TOKEN"], "secret")
        self.assertNotIn("HOME", env)


if __name__ == "__main__":
    unittest.main()
