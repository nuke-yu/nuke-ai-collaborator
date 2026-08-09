import os
import tempfile
import unittest

from channels.bridge import BindingStatus, ChannelBinding, OutboundEventProjector, OutboundPolicyError
from channels.stores import ChannelStore


def active_binding(**overrides):
    values = {
        "binding_id": "binding-1",
        "channel_instance_id": "slack:prod",
        "external_tenant_id": "tenant-a",
        "external_conversation_id": "chat-1",
        "group_id": 7,
        "default_bot_id": 42,
        "allowed_bot_ids": (42, 43),
        "status": BindingStatus.ACTIVE,
        "config_version": 3,
    }
    values.update(overrides)
    return ChannelBinding(**values)


class TestChannelOutbound(unittest.IsolatedAsyncioTestCase):
    async def test_group_event_is_projected_with_binding_snapshot(self):
        envelope = OutboundEventProjector(active_binding()).project(
            "workflow.completed", {"summary": "done"}, event_id="event-1", session_id="s-1"
        )
        self.assertEqual(envelope.identity.channel, "slack")
        self.assertEqual(envelope.conversation.external_conversation_id, "chat-1")
        self.assertEqual(envelope.payload["event"], {"summary": "done"})
        self.assertEqual(envelope.payload["config_version"], 3)
        self.assertEqual(envelope.idempotency_key, "event-1")

    async def test_policy_filters_events_and_suspended_binding_fails_closed(self):
        projector = OutboundEventProjector(active_binding(outbound_policy={"events": ["task_stuck"]}))
        with self.assertRaises(OutboundPolicyError):
            projector.project("workflow.completed", {})
        with self.assertRaises(OutboundPolicyError):
            OutboundEventProjector(active_binding(status=BindingStatus.SUSPENDED))

    async def test_projection_can_enqueue_in_channel_owned_outbox_idempotently(self):
        tmp = tempfile.TemporaryDirectory(prefix="channel-outbound-")
        try:
            store = ChannelStore(os.path.join(tmp.name, "channel.db"))
            await store.initialize()
            projector = OutboundEventProjector(active_binding())
            self.assertTrue(await projector.enqueue(store, "task_stuck", {"task": "T1"}, event_id="event-2"))
            self.assertFalse(await projector.enqueue(store, "task_stuck", {"task": "T1"}, event_id="event-2"))
            delivery = await store.get_delivery("event-2")
            self.assertEqual(delivery["group_id"], 7)
            self.assertEqual(delivery["payload"]["binding_id"], "binding-1")
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
