import os
import tempfile
import unittest

from channels.bridge import (
    BindingStatus,
    ChannelBinding,
    ChannelBindingStore,
    IntegrationMember,
    IntegrationMemberStore,
)
from channels.core import ChannelConversation, ChannelIdentity, InboundEnvelope
from channels.inbound_runtime import ChannelInboundService
from channels.stores import ChannelStore


class TestChannelInboundService(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_message_is_deduplicated_and_targets_configured_bot(self):
        with tempfile.TemporaryDirectory(prefix="channel-inbound-runtime-") as directory:
            path = os.path.join(directory, "channel.db")
            channel_store = ChannelStore(path)
            bindings = ChannelBindingStore(path)
            members = IntegrationMemberStore(path)
            await channel_store.initialize()
            await bindings.initialize()
            await members.initialize()
            binding = ChannelBinding(
                binding_id="binding-1", channel_instance_id="feishu:prod",
                external_tenant_id="tenant-1", external_conversation_id="chat-1",
                group_id=7, default_bot_id=42,
            )
            await bindings.create(binding)
            await bindings.transition("binding-1", BindingStatus.PENDING_APPROVAL)
            await bindings.transition("binding-1", BindingStatus.ACTIVE)
            await members.create(IntegrationMember(
                integration_member_id=91, binding_id="binding-1", group_id=7,
                channel_instance_id="feishu:prod", display_name="飞书",
            ))
            dispatched = []
            service = ChannelInboundService(
                channel_store, bindings, members,
                lambda route, member: _capture(dispatched, route, member),
            )
            envelope = InboundEnvelope(
                identity=ChannelIdentity("feishu", "tenant-1", "user-1"),
                conversation=ChannelConversation("chat-1"),
                external_message_id="message-1", text="请检查构建",
            )
            route = await service.ingest("FEISHU:PROD", envelope)
            self.assertEqual(route.target_bot_id, 42)
            self.assertEqual(dispatched[0][1].integration_member_id, 91)
            self.assertIsNone(await service.ingest("feishu:prod", envelope))
            self.assertEqual(len(dispatched), 1)


async def _capture(items, route, member):
    items.append((route, member))


if __name__ == "__main__":
    unittest.main()
