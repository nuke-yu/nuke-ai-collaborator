import os
import tempfile
import unittest

from channels.core import ChannelConversation, ChannelIdentity, InboundEnvelope, OutboundEnvelope
from channels.stores import ChannelStore, DeliveryState


class TestChannelStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="channel-store-")
        self.store = ChannelStore(os.path.join(self.tmp.name, "channel.db"))
        await self.store.initialize()
        self.inbound = InboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a", "user-1"),
            conversation=ChannelConversation("chat-1"),
            external_message_id="msg-1",
            text="run task",
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_inbound_is_deduplicated_in_channel_store(self):
        self.assertTrue(await self.store.record_inbound(self.inbound))
        self.assertFalse(await self.store.record_inbound(self.inbound))

    async def test_outbound_claim_and_success_are_idempotent(self):
        outbound = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"),
            conversation=ChannelConversation("chat-1"),
            event_type="workflow.completed",
            payload={"summary": "done"},
            idempotency_key="event-1",
        )
        self.assertTrue(await self.store.enqueue_outbound(outbound))
        self.assertFalse(await self.store.enqueue_outbound(outbound))
        claimed = await self.store.claim_due_delivery()
        self.assertEqual(claimed["state"], DeliveryState.SENDING)
        self.assertEqual(claimed["attempts"], 1)
        self.assertTrue(await self.store.mark_sent("event-1", "external-msg-1"))
        self.assertFalse(await self.store.mark_sent("event-1", "external-msg-2"))
        stored = await self.store.get_delivery("event-1")
        self.assertEqual(stored["state"], DeliveryState.SENT)
        self.assertEqual(stored["external_message_id"], "external-msg-1")

    async def test_failed_delivery_can_retry_or_enter_dead_letter(self):
        outbound = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"),
            conversation=ChannelConversation("chat-1"),
            event_type="workflow.failed",
            payload={"summary": "failed"},
            idempotency_key="event-2",
        )
        await self.store.enqueue_outbound(outbound)
        await self.store.claim_due_delivery()
        self.assertTrue(await self.store.mark_failed("event-2", "timeout", retry_at_ms=0))
        retry = await self.store.claim_due_delivery(now_ms=1)
        self.assertEqual(retry["state"], DeliveryState.SENDING)
        self.assertTrue(await self.store.mark_failed("event-2", "permanent", retry_at_ms=None))
        stored = await self.store.get_delivery("event-2")
        self.assertEqual(stored["state"], DeliveryState.DEAD_LETTER)


if __name__ == "__main__":
    unittest.main()
