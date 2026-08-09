import os
import tempfile
import unittest

from channels.core import ChannelConversation, ChannelIdentity, InboundEnvelope, OutboundEnvelope
from channels.stores import ChannelStore, DeliveryState, sanitize_text_for_storage


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

    async def test_pause_health_and_dead_letter_replay_are_operator_safe(self):
        outbound = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"), conversation=ChannelConversation("chat-1"),
            event_type="workflow.failed", payload={"summary": "failed"}, idempotency_key="event-3",
        )
        await self.store.enqueue_outbound(outbound)
        await self.store.set_channel_paused("slack", True)
        self.assertIsNone(await self.store.claim_due_delivery())
        health = await self.store.get_delivery_health()
        self.assertEqual(health["paused_channels"], ["slack"])
        await self.store.set_channel_paused("slack", False)
        await self.store.claim_due_delivery()
        self.assertTrue(await self.store.mark_failed("event-3", "permanent"))
        self.assertTrue(await self.store.replay_dead_letter("event-3"))
        self.assertFalse(await self.store.replay_dead_letter("event-3"))
        self.assertEqual((await self.store.get_delivery("event-3"))["state"], DeliveryState.RETRYING)
        audit = await self.store.list_audit("event-3")
        self.assertEqual(audit[-1]["event_type"], "delivery.replayed")

    async def test_oversized_lowercase_bearer_is_not_stored_raw(self):
        value = "x" * 70_000 + " authorization: bearer " + "a" * 40
        safe = sanitize_text_for_storage(value)
        self.assertNotIn("authorization: bearer", safe.lower())
        self.assertNotIn("a" * 40, safe)


if __name__ == "__main__":
    unittest.main()
