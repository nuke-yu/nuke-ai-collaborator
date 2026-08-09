import os
import tempfile
import unittest

import aiosqlite

from channels.bridge import ChannelBinding, BindingStatus, GroupChannelOutboxError, GroupChannelOutboxRelay, GroupChannelOutboxWriter, GroupRelayResult, initialize_group_channel_outbox
from channels.bridge.outbound import OutboundEventProjector
from channels.stores import ChannelStore


class TestChannelGroupOutbox(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="group-channel-outbox-")
        self.group_path = os.path.join(self.tmp.name, "group.db")
        self.channel = ChannelStore(os.path.join(self.tmp.name, "channel.db"))
        await self.channel.initialize()
        self.binding = ChannelBinding(
            binding_id="binding-1", channel_instance_id="slack:prod", external_tenant_id="tenant-a",
            external_conversation_id="chat-1", group_id=7, default_bot_id=42, status=BindingStatus.ACTIVE,
        )
        self.envelope = OutboundEventProjector(self.binding).project("task_stuck", {"message": "stop"}, event_id="event-1")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_group_event_is_durable_only_when_business_transaction_commits(self):
        async with aiosqlite.connect(self.group_path) as db:
            await initialize_group_channel_outbox(db)
            await db.commit()
            with self.assertRaises(GroupChannelOutboxError):
                await GroupChannelOutboxWriter.append(db, self.envelope)
            await db.execute("BEGIN")
            self.assertTrue(await GroupChannelOutboxWriter.append(db, self.envelope))
            await db.commit()
        relay = GroupChannelOutboxRelay(self.group_path, self.channel, owner_id="relay-1")
        self.assertEqual(await relay.relay_once(), GroupRelayResult.FORWARDED)
        self.assertEqual((await self.channel.get_delivery(self.envelope.idempotency_key))["state"], "pending")
        self.assertEqual(await relay.relay_once(), GroupRelayResult.IDLE)

    async def test_relay_replay_after_channel_enqueue_is_idempotent(self):
        async with aiosqlite.connect(self.group_path) as db:
            await db.execute("BEGIN")
            await GroupChannelOutboxWriter.append(db, self.envelope)
            await db.commit()
        relay = GroupChannelOutboxRelay(self.group_path, self.channel, owner_id="relay-1")
        self.assertEqual(await relay.relay_once(), GroupRelayResult.FORWARDED)
        async with aiosqlite.connect(self.group_path) as db:
            await db.execute("UPDATE group_channel_event_outbox SET state='pending',lease_owner=NULL,lease_expires_at=NULL WHERE source_event_id='event-1'")
            await db.commit()
        self.assertEqual(await relay.relay_once(), GroupRelayResult.FORWARDED)
        self.assertEqual((await self.channel.get_delivery(self.envelope.idempotency_key))["idempotency_key"], self.envelope.idempotency_key)


if __name__ == "__main__":
    unittest.main()
