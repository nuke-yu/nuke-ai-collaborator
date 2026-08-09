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

    async def test_missing_event_id_fails_and_distinct_events_are_not_collapsed(self):
        projector = OutboundEventProjector(active_binding())
        with self.assertRaises(ValueError):
            projector.project("task_stuck", {})
        first = projector.project("task_stuck", {}, event_id="event-1")
        second = projector.project("task_stuck", {}, event_id="event-2")
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)


if __name__ == "__main__":
    unittest.main()
