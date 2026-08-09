import unittest

from channels.core import (
    BridgeDirection,
    BridgeEnvelope,
    ChannelConversation,
    ChannelIdentity,
    DeliveryReceipt,
    InboundEnvelope,
    OutboundEnvelope,
    canonical_message_key,
)


class TestChannelContracts(unittest.TestCase):
    def test_message_key_is_unambiguous_for_delimiter_containing_ids(self):
        self.assertNotEqual(
            canonical_message_key("a:b", "c", "d", "e"),
            canonical_message_key("a", "b:c", "d", "e"),
        )

    def test_protocol_versions_are_validated(self):
        with self.assertRaises(ValueError):
            InboundEnvelope(
                identity=ChannelIdentity("slack", "tenant"),
                conversation=ChannelConversation("chat"),
                external_message_id="message",
                protocol_version="channel.v0",
            )
        with self.assertRaises(ValueError):
            BridgeEnvelope(
                direction=BridgeDirection.OUTBOUND,
                event_type="event",
                idempotency_key="event-1",
                payload={},
                protocol_version="channel-bridge.v0",
            )
    def setUp(self):
        self.identity = ChannelIdentity("Feishu", "tenant-a", "user-1")
        self.conversation = ChannelConversation("chat-1")

    def test_inbound_is_standalone_before_group_binding(self):
        envelope = InboundEnvelope(
            identity=self.identity,
            conversation=self.conversation,
            external_message_id="msg-1",
            text="hello",
        )
        self.assertEqual(envelope.channel, "feishu")
        self.assertIsNone(envelope.group_id)
        self.assertEqual(envelope.idempotency_key, "channel.v1|6:feishu8:tenant-a6:chat-15:msg-1")
        self.assertEqual(envelope.to_dict()["protocol_version"], "channel.v1")

    def test_bridge_requires_group_after_explicit_binding(self):
        with self.assertRaises(ValueError):
            BridgeEnvelope(
                direction=BridgeDirection.INBOUND,
                event_type="message.received",
                idempotency_key="key-1",
                payload={"text": "hello"},
            )
        bridge = BridgeEnvelope(
            direction=BridgeDirection.INBOUND,
            event_type="message.received",
            idempotency_key="key-1",
            payload={"text": "hello"},
            binding_id="binding-1",
            group_id=7,
            integration_member_id=91,
        )
        self.assertEqual(bridge.to_dict()["direction"], BridgeDirection.INBOUND)

    def test_outbound_and_delivery_contracts_validate_status(self):
        outbound = OutboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a"),
            conversation=self.conversation,
            event_type="workflow.completed",
            payload={"summary": "done"},
            idempotency_key="event-1",
            group_id=7,
            session_id="session-1",
        )
        self.assertEqual(outbound.to_dict()["event_type"], "workflow.completed")
        with self.assertRaises(ValueError):
            DeliveryReceipt("slack", "event-1", "sent")
        receipt = DeliveryReceipt("slack", "event-1", "sent", external_message_id="msg-2")
        self.assertEqual(receipt.to_dict()["status"], "sent")

    def test_contracts_reject_non_json_payload_and_invalid_member_pair(self):
        with self.assertRaises(ValueError):
            OutboundEnvelope(
                identity=self.identity,
                conversation=self.conversation,
                event_type="message",
                payload={"value": object()},
                idempotency_key="event-1",
            )
        with self.assertRaises(ValueError):
            InboundEnvelope(
                identity=self.identity,
                conversation=self.conversation,
                external_message_id="msg-1",
                member_id=42,
            )


if __name__ == "__main__":
    unittest.main()
