import os
import tempfile
import unittest

from channels.bridge import (
    BindingStatus,
    ChannelBinding,
    ChannelBindingStore,
    IntegrationMember,
    IntegrationMemberStatus,
    IntegrationMemberStore,
)


class TestIntegrationMember(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="channel-member-")
        path = os.path.join(self.tmp.name, "bridge.db")
        self.bindings = ChannelBindingStore(path)
        self.members = IntegrationMemberStore(path)
        await self.bindings.initialize()
        await self.members.initialize()
        binding = ChannelBinding(
            binding_id="binding-1", channel_instance_id="slack-prod",
            external_tenant_id="tenant-a", external_conversation_id="chat-1",
            group_id=7, default_bot_id=42, created_by="user:1",
        )
        await self.bindings.create(binding)
        await self.bindings.transition("binding-1", BindingStatus.PENDING_APPROVAL)
        await self.bindings.transition("binding-1", BindingStatus.ACTIVE)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_integration_member_is_not_a_bot_and_cannot_execute(self):
        member = IntegrationMember(
            integration_member_id=91,
            binding_id="binding-1",
            group_id=7,
            channel_instance_id="slack-prod",
            display_name="Slack 集成",
            metadata={"external_conversation_id": "chat-1"},
        )
        await self.members.create(member)
        stored = await self.members.get(91)
        self.assertEqual(stored.member_type, "integration")
        self.assertFalse(stored.can_execute_tools)
        self.assertEqual(stored.to_member_dict()["type"], "integration")

    async def test_member_requires_active_matching_binding(self):
        member = IntegrationMember(
            integration_member_id=92, binding_id="binding-missing", group_id=7,
            channel_instance_id="slack-prod", display_name="Missing",
        )
        with self.assertRaises(ValueError):
            await self.members.create(member)

    async def test_member_status_can_be_suspended(self):
        member = IntegrationMember(
            integration_member_id=93, binding_id="binding-1", group_id=7,
            channel_instance_id="slack-prod", display_name="Slack 集成",
        )
        await self.members.create(member)
        self.assertTrue(await self.members.set_status(93, IntegrationMemberStatus.SUSPENDED))
        self.assertEqual((await self.members.get(93)).status, IntegrationMemberStatus.SUSPENDED)

    async def test_group_projection_lists_integration_members_without_bot_semantics(self):
        member = IntegrationMember(
            integration_member_id=95, binding_id="binding-1", group_id=7,
            channel_instance_id="slack-prod", display_name="Slack 集成",
        )
        await self.members.create(member)
        projected = await self.members.list_for_group(7)
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0].to_member_dict()["type"], "integration")
        self.assertFalse(projected[0].to_member_dict()["can_execute_tools"])

    async def test_revoked_binding_cannot_reactivate_member(self):
        member = IntegrationMember(
            integration_member_id=94, binding_id="binding-1", group_id=7,
            channel_instance_id="slack-prod", display_name="Slack 集成",
        )
        await self.members.create(member)
        await self.bindings.transition("binding-1", BindingStatus.REVOKED)
        self.assertEqual((await self.members.get(94)).status, IntegrationMemberStatus.REVOKED)
        with self.assertRaises(ValueError):
            await self.members.set_status(94, IntegrationMemberStatus.ACTIVE)


if __name__ == "__main__":
    unittest.main()
