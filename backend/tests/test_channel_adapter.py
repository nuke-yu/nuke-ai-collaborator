import hashlib
import hmac
import json
import unittest
import asyncio
import time

from channels import ChannelAdapter, ChannelAuthError


class TestChannelAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.secret = "channel-secret"

    def body(self, payload):
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    def signature(self, payload, timestamp=None):
        body = self.body(payload)
        timestamp = int(time.time()) if timestamp is None else timestamp
        return hmac.new(self.secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()

    async def test_normalizes_authorized_message_and_deduplicates(self):
        async def resolve(tenant, external_group):
            return (7, 42) if (tenant, external_group) == ("tenant-a", "room-1") else None

        seen = set()
        lock = asyncio.Lock()

        async def record(envelope):
            async with lock:
                if envelope.idempotency_key in seen:
                    return False
                seen.add(envelope.idempotency_key)
                return True

        adapter = ChannelAdapter(channel="webhook", secret=self.secret, resolve_group=resolve, record_inbound=record)
        payload = {
            "tenant_id": "tenant-a", "group_id": "room-1", "user_id": "u-9",
            "message_id": "m-1", "text": "@bot review this", "mentions": ["bot"],
            "reply_to_id": "m-0", "attachments": [{"name": "a.txt"}],
        }
        envelope = await adapter.normalize(payload, raw_body=self.body(payload), signature=self.signature(payload), timestamp=int(time.time()))
        self.assertEqual(envelope.group_id, 7)
        self.assertEqual(envelope.mentions, ("bot",))
        self.assertEqual(envelope.reply_to_external_id, "m-0")
        self.assertIsNone(await adapter.normalize(payload, raw_body=self.body(payload), signature=self.signature(payload), timestamp=int(time.time())))

    async def test_without_store_callback_normalize_does_not_use_process_memory_dedup(self):
        async def resolve(*_):
            return (7, 42)
        adapter = ChannelAdapter(channel="webhook", secret=self.secret, resolve_group=resolve)
        payload = {"tenant_id": "t", "group_id": "r", "user_id": "u", "message_id": "m"}
        first = await adapter.normalize(payload, raw_body=self.body(payload), signature=self.signature(payload), timestamp=int(time.time()))
        second_payload = {**payload, "message_id": "m-2"}
        second = await adapter.normalize(second_payload, raw_body=self.body(second_payload), signature=self.signature(second_payload), timestamp=int(time.time()))
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

    async def test_rejects_bad_signature_and_unauthorized_group(self):
        async def resolve_group(*_):
            return None

        adapter = ChannelAdapter(channel="webhook", secret=self.secret, resolve_group=resolve_group)
        payload = {"tenant_id": "t", "group_id": "r", "user_id": "u", "message_id": "m"}
        with self.assertRaises(ChannelAuthError):
            await adapter.normalize(payload, raw_body=self.body(payload), signature="bad", timestamp=int(time.time()))
        with self.assertRaises(ChannelAuthError):
            await adapter.normalize(payload, raw_body=self.body(payload), signature=self.signature(payload), timestamp=1, now=1000)


if __name__ == "__main__":
    unittest.main()
