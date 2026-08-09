import os
import tempfile
import asyncio
import unittest

from channels.bridge import BindingConflictError, BindingStatus, ChannelBinding, ChannelBindingStore


class TestChannelBinding(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="channel-binding-")
        self.store = ChannelBindingStore(os.path.join(self.tmp.name, "bindings.db"))
        await self.store.initialize()
        self.binding = ChannelBinding(
            binding_id="binding-1",
            channel_instance_id="slack-prod",
            external_tenant_id="tenant-a",
            external_conversation_id="chat-1",
            group_id=7,
            default_bot_id=42,
            allowed_bot_ids=(43,),
            created_by="user:1",
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_binding_requires_active_transition(self):
        await self.store.create(self.binding)
        await self.store.transition("binding-1", BindingStatus.PENDING_APPROVAL)
        active = await self.store.transition("binding-1", BindingStatus.ACTIVE)
        self.assertEqual(active.status, BindingStatus.ACTIVE)
        self.assertEqual(active.config_version, 3)
        self.assertEqual(active.allowed_bot_ids, (42, 43))

    async def test_invalid_transition_and_duplicate_conversation_are_rejected(self):
        await self.store.create(self.binding)
        with self.assertRaises(ValueError):
            await self.store.transition("binding-1", BindingStatus.ACTIVE)
        with self.assertRaises(ValueError):
            await self.store.create(ChannelBinding(**{**self.binding.to_dict(), "binding_id": "binding-2"}))

    async def test_revoked_binding_is_terminal(self):
        await self.store.create(self.binding)
        revoked = await self.store.transition("binding-1", BindingStatus.REVOKED)
        self.assertEqual(revoked.status, BindingStatus.REVOKED)
        with self.assertRaises(ValueError):
            await self.store.transition("binding-1", BindingStatus.ACTIVE)

    async def test_concurrent_transition_cannot_report_two_successes(self):
        await self.store.create(self.binding)
        results = await asyncio.gather(
            self.store.transition("binding-1", BindingStatus.PENDING_APPROVAL),
            self.store.transition("binding-1", BindingStatus.PENDING_APPROVAL),
            return_exceptions=True,
        )
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        self.assertTrue(any(isinstance(result, (BindingConflictError, ValueError)) for result in results))


if __name__ == "__main__":
    unittest.main()
