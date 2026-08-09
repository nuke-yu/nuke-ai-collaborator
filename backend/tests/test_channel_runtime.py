import os
import tempfile
import time
import unittest

from channels.connectors import ConnectorError
from channels.core import ChannelConversation, ChannelIdentity, DeliveryReceipt, OutboundEnvelope
from channels.runtime import ChannelDeliveryDispatcher, ChannelDeliveryError
from channels.stores import ChannelStore, DeliveryState


class FailingConnector:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    async def send(self, envelope):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectorError("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
        return DeliveryReceipt("slack", envelope.idempotency_key, "sent", external_message_id="external-1")


class TestChannelRuntime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="channel-runtime-")
        self.store = ChannelStore(os.path.join(self.tmp.name, "channel.db"))
        await self.store.initialize()
        await self.store.enqueue_outbound(OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"),
            conversation=ChannelConversation("chat-1"),
            event_type="task_stuck",
            payload={"token": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456", "long": "x" * 20_000},
            idempotency_key="event-1",
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_payload_is_redacted_and_retry_event_is_audited(self):
        connector = FailingConnector(1)
        dispatcher = ChannelDeliveryDispatcher(self.store, connector, max_attempts=2, base_delay_ms=0)
        self.assertTrue(await dispatcher.run_once())
        stored = await self.store.get_delivery("event-1")
        self.assertEqual(stored["state"], DeliveryState.RETRYING)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", stored["payload"]["token"])
        self.assertLessEqual(len(stored["payload"]["long"]), 10_000)
        self.assertTrue(await dispatcher.run_once())
        self.assertEqual((await self.store.get_delivery("event-1"))["state"], DeliveryState.SENT)
        audit = await self.store.list_audit("event-1")
        self.assertEqual([item["event_type"] for item in audit], ["delivery.retrying", "delivery.sent"])
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", str(audit))

    async def test_raw_pem_and_secret_keys_are_redacted_before_storage(self):
        pem = "x" * 12_000 + "-----BEGIN RSA PRIVATE KEY-----\nsecret-material\n-----END RSA PRIVATE KEY-----"
        from channels.core import ChannelConversation, ChannelIdentity, InboundEnvelope

        inbound = InboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a", "user-1"),
            conversation=ChannelConversation("chat-raw"),
            external_message_id="raw-1",
            text="hello",
            raw={"Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456": pem},
        )
        self.assertTrue(await self.store.record_inbound(inbound))
        async with __import__("aiosqlite").connect(self.store.path) as db:
            cursor = await db.execute("SELECT payload_json FROM channel_messages WHERE message_key=?", (inbound.idempotency_key,))
            raw = (await cursor.fetchone())[0]
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", raw)
        self.assertNotIn("PRIVATE KEY-----", raw)

    async def test_last_error_is_redacted_before_storage(self):
        dispatcher = ChannelDeliveryDispatcher(self.store, FailingConnector(9), max_attempts=1)
        await dispatcher.run_once()
        stored = await self.store.get_delivery("event-1")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", stored["last_error"])

    async def test_permanent_failure_enters_dead_letter_after_limit(self):
        dispatcher = ChannelDeliveryDispatcher(self.store, FailingConnector(9), max_attempts=2, base_delay_ms=0)
        await dispatcher.run_once()
        await dispatcher.run_once(now_ms=int(time.time() * 1000) + 1)
        stored = await self.store.get_delivery("event-1")
        self.assertEqual(stored["state"], DeliveryState.DEAD_LETTER)
        self.assertEqual((await self.store.list_audit("event-1"))[-1]["event_type"], "delivery.dead_letter")

    async def test_failed_receipt_never_becomes_sent(self):
        class FailedReceiptConnector:
            async def send(self, envelope):
                return DeliveryReceipt("slack", envelope.idempotency_key, "failed", error_code="rate_limited")

        dispatcher = ChannelDeliveryDispatcher(self.store, FailedReceiptConnector(), max_attempts=1)
        await dispatcher.run_once()
        stored = await self.store.get_delivery("event-1")
        self.assertEqual(stored["state"], DeliveryState.DEAD_LETTER)
        self.assertIsNone(stored["external_message_id"])

    async def test_mismatched_receipt_is_rejected(self):
        class MismatchedConnector:
            async def send(self, envelope):
                return DeliveryReceipt("other", envelope.idempotency_key, "sent", external_message_id="remote")

        dispatcher = ChannelDeliveryDispatcher(self.store, MismatchedConnector(), max_attempts=1)
        await dispatcher.run_once()
        stored = await self.store.get_delivery("event-1")
        self.assertEqual(stored["state"], DeliveryState.DEAD_LETTER)
        self.assertIn("channel mismatch", stored["last_error"])

    async def test_expired_sending_lease_is_reclaimed_after_worker_crash(self):
        claimed = await self.store.claim_due_delivery(lease_owner="worker-a", lease_ms=10, now_ms=int(time.time() * 1000))
        self.assertEqual(claimed["state"], DeliveryState.SENDING)
        self.assertEqual(claimed["lease_owner"], "worker-a")
        recovered = await self.store.recover_expired_deliveries(now_ms=int(time.time() * 1000) + 11)
        self.assertEqual(recovered, 1)
        reclaimed = await self.store.claim_due_delivery(lease_owner="worker-b", lease_ms=100, now_ms=int(time.time() * 1000) + 12)
        self.assertEqual(reclaimed["state"], DeliveryState.SENDING)
        self.assertEqual(reclaimed["lease_owner"], "worker-b")


if __name__ == "__main__":
    unittest.main()
