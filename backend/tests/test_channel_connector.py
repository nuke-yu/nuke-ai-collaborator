import hashlib
import hmac
import json
import unittest
import time

from channels.connectors import ConnectorAuthError, ConnectorError, SignedWebhookConnector
from channels.core import ChannelConversation, ChannelIdentity, OutboundEnvelope


class TestSignedWebhookConnector(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.secret = "connector-secret"
        self.connector = SignedWebhookConnector(channel="reference", secret=self.secret, allow_in_memory_replay_guard=True)

    def body(self, payload):
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    def signature(self, payload, timestamp=None):
        body = self.body(payload)
        timestamp = int(time.time()) if timestamp is None else timestamp
        return hmac.new(self.secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()

    async def test_normalizes_without_group_dependency(self):
        payload = {
            "tenant_id": "tenant-a",
            "conversation_id": "chat-1",
            "user_id": "u-1",
            "message_id": "m-1",
            "text": "hello",
            "mentions": ["dev"],
        }
        timestamp = int(time.time())
        envelope = await self.connector.normalize(payload, raw_body=self.body(payload), signature=self.signature(payload, timestamp), timestamp=timestamp)
        self.assertEqual(envelope.channel, "reference")
        self.assertIsNone(envelope.group_id)
        self.assertEqual(envelope.external_group_id, "chat-1")
        self.assertEqual(envelope.mentions, ("dev",))

    async def test_rejects_bad_signature_and_malformed_payload(self):
        payload = {"tenant_id": "tenant-a", "conversation_id": "chat-1", "user_id": "u-1", "message_id": "m-1"}
        with self.assertRaises(ConnectorAuthError):
            await self.connector.normalize(payload, raw_body=self.body(payload), signature="bad", timestamp=int(time.time()))
        malformed = {"tenant_id": "tenant-a", "conversation_id": "chat-1"}
        with self.assertRaises(ConnectorError):
            await self.connector.normalize(malformed, raw_body=self.body(malformed), signature=self.signature(malformed), timestamp=int(time.time()))

    async def test_rejects_mapping_without_raw_body_and_replay_or_stale_timestamp(self):
        payload = {"tenant_id": "tenant-a", "conversation_id": "chat-1", "user_id": "u-1", "message_id": "m-1"}
        with self.assertRaises(ConnectorError):
            await self.connector.normalize(payload, signature=self.signature(payload), timestamp=int(time.time()))
        body = self.body(payload)
        signature = self.signature(payload, 100)
        await self.connector.normalize(payload, raw_body=body, signature=signature, timestamp=100, now=100)
        with self.assertRaises(ConnectorAuthError):
            await self.connector.normalize(payload, raw_body=body, signature=signature, timestamp=101, now=101)
        with self.assertRaises(ConnectorAuthError):
            await self.connector.normalize({**payload, "message_id": "m-2"}, raw_body=self.body({**payload, "message_id": "m-2"}), signature=self.signature({**payload, "message_id": "m-2"}), timestamp=1, now=1000)

    async def test_production_mode_requires_durable_replay_guard(self):
        payload = {"tenant_id": "tenant-a", "conversation_id": "chat-1", "user_id": "u-1", "message_id": "m-3"}
        timestamp = int(time.time())
        with self.assertRaises(ConnectorAuthError):
            await self.connector.__class__(channel="reference", secret=self.secret).normalize(
                payload, raw_body=self.body(payload), signature=self.signature(payload, timestamp), timestamp=timestamp
            )

    async def test_sends_and_returns_receipt(self):
        sent = []

        async def send(envelope):
            sent.append(envelope)
            return "external-m-2"

        connector = SignedWebhookConnector(channel="reference", secret=self.secret, send=send)
        envelope = OutboundEnvelope(
            identity=ChannelIdentity("reference", "tenant-a"),
            conversation=ChannelConversation("chat-1"),
            event_type="workflow.completed",
            payload={"summary": "done"},
            idempotency_key="event-1",
        )
        receipt = await connector.send(envelope)
        self.assertEqual(receipt.status, "sent")
        self.assertEqual(receipt.external_message_id, "external-m-2")
        self.assertEqual(sent[0].event_type, "workflow.completed")

    async def test_outbound_transport_failures_are_normalized(self):
        async def send(_):
            raise TimeoutError("upstream timeout")

        connector = SignedWebhookConnector(channel="reference", secret=self.secret, send=send)
        envelope = OutboundEnvelope(
            identity=ChannelIdentity("reference", "tenant-a"),
            conversation=ChannelConversation("chat-1"),
            event_type="message",
            payload={},
            idempotency_key="event-2",
        )
        with self.assertRaisesRegex(ConnectorError, "outbound webhook failed"):
            await connector.send(envelope)
