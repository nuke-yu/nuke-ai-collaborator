import os
import tempfile
import time
import unittest

from channels.connectors import ConnectorError
from channels.core import ChannelConversation, ChannelIdentity, DeliveryReceipt, OutboundEnvelope
from channels.runtime import ChannelDeliveryDispatcher
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

    async def test_permanent_failure_enters_dead_letter_after_limit(self):
        dispatcher = ChannelDeliveryDispatcher(self.store, FailingConnector(9), max_attempts=2, base_delay_ms=0)
        await dispatcher.run_once()
        await dispatcher.run_once(now_ms=int(time.time() * 1000) + 1)
        stored = await self.store.get_delivery("event-1")
        self.assertEqual(stored["state"], DeliveryState.DEAD_LETTER)
        self.assertEqual((await self.store.list_audit("event-1"))[-1]["event_type"], "delivery.dead_letter")


if __name__ == "__main__":
    unittest.main()
