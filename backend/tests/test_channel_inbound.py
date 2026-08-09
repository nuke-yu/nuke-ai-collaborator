import unittest

from channels.bridge import (
    BindingStatus,
    ChannelBinding,
    InboundBotRouter,
    InboundRouteError,
)
from channels.core import ChannelConversation, ChannelIdentity, InboundEnvelope


class TestChannelInboundRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.binding = ChannelBinding(
            binding_id="binding-1", channel_instance_id="slack:prod",
            external_tenant_id="tenant-a", external_conversation_id="chat-1",
            group_id=7, default_bot_id=42, allowed_bot_ids=(43,),
            mention_required=False, status=BindingStatus.ACTIVE,
        )
        self.router = InboundBotRouter(self.binding, integration_member_id=91)
        self.envelope = InboundEnvelope(
            identity=ChannelIdentity("slack", "tenant-a", "user-1"),
            conversation=ChannelConversation("chat-1"),
            external_message_id="msg-1", text="please help",
        )

    async def test_default_bot_route_creates_group_bridge_envelope(self):
        route = self.router.route(self.envelope)
        self.assertEqual(route.group_id, 7)
        self.assertEqual(route.target_bot_id, 42)
        self.assertEqual(route.bridge_envelope.payload["target_bot_id"], 42)
        self.assertEqual(route.bridge_envelope.integration_member_id, 91)

    async def test_mention_selects_allowed_bot_and_dispatches_structured_envelope(self):
        received = []

        async def dispatch(envelope):
            received.append(envelope)

        mentioned = InboundEnvelope(
            identity=self.envelope.identity,
            conversation=self.envelope.conversation,
            external_message_id="msg-2", text="test", mentions=("@qa",),
        )
        route = await self.router.dispatch(mentioned, dispatch, bot_mentions={"qa": 43})
        self.assertEqual(route.target_bot_id, 43)
        self.assertEqual(received[0].event_type, "message.received")

    async def test_mention_required_and_scope_mismatch_fail_closed(self):
        binding = ChannelBinding(**{**self.binding.to_dict(), "mention_required": True})
        router = InboundBotRouter(binding, integration_member_id=91)
        with self.assertRaises(InboundRouteError):
            router.route(self.envelope)
        wrong = InboundEnvelope(
            identity=self.envelope.identity,
            conversation=ChannelConversation("other-chat"),
            external_message_id="msg-3",
        )
        with self.assertRaises(InboundRouteError):
            self.router.route(wrong)

    async def test_disallowed_or_ambiguous_mentions_are_rejected(self):
        disallowed = InboundEnvelope(
            identity=self.envelope.identity,
            conversation=self.envelope.conversation,
            external_message_id="msg-4", mentions=("@unknown",),
        )
        with self.assertRaises(InboundRouteError):
            InboundBotRouter(
                ChannelBinding(**{**self.binding.to_dict(), "mention_required": True}),
                integration_member_id=91,
            ).route(disallowed, bot_mentions={"unknown": 99})
        ambiguous = InboundEnvelope(
            identity=self.envelope.identity,
            conversation=self.envelope.conversation,
            external_message_id="msg-5", mentions=("@dev", "@qa"),
        )
        with self.assertRaises(InboundRouteError):
            self.router.route(ambiguous, bot_mentions={"dev": 42, "qa": 43})

    async def test_router_refreshes_binding_state_before_each_route(self):
        current = [self.binding]
        router = InboundBotRouter(self.binding, integration_member_id=91, binding_provider=lambda: current[0])
        self.assertEqual(router.route(self.envelope).target_bot_id, 42)
        current[0] = self.binding.transitioned(BindingStatus.SUSPENDED)
        with self.assertRaises(InboundRouteError):
            router.route(self.envelope)


if __name__ == "__main__":
    unittest.main()
