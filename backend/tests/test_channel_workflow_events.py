import os
import tempfile
import unittest

import aiosqlite

from channels.bridge import BindingStatus, ChannelBinding, ChannelBindingStore, append_workflow_channel_events
from channels.bridge.group_outbox import initialize_group_channel_outbox


class TestChannelWorkflowEvents(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_observation_is_written_to_group_outbox_in_caller_transaction(self):
        tmp = tempfile.TemporaryDirectory(prefix="channel-workflow-events-")
        try:
            binding_store = ChannelBindingStore(os.path.join(tmp.name, "bridge.db"))
            await binding_store.initialize()
            await binding_store.create(ChannelBinding(
                binding_id="binding-1", channel_instance_id="slack:prod",
                external_tenant_id="tenant-a", external_conversation_id="chat-1",
                group_id=7, default_bot_id=42, status=BindingStatus.ACTIVE,
            ), allow_active=True)
            group_path = os.path.join(tmp.name, "group.db")
            async with aiosqlite.connect(group_path) as db:
                await initialize_group_channel_outbox(db)
                await db.commit()
                await db.execute("BEGIN")
                count = await append_workflow_channel_events(db, 7, [{
                    "event_id": "workflow-event-1",
                    "event_type": "workflow_completed",
                    "payload": {"summary": "done"},
                    "context": {"workflow_id": "wf-1", "session_id": "s-1"},
                }], binding_store)
                self.assertEqual(count, 1)
                await db.commit()
                async with db.execute("SELECT state,payload_json,source_event_id FROM group_channel_event_outbox") as cursor:
                    row = await cursor.fetchone()
            self.assertEqual(row[0], "pending")
            self.assertIn("workflow_id", row[1])
            self.assertEqual(row[2], "workflow-event-1")
        finally:
            tmp.cleanup()

    async def test_one_source_event_gets_one_projection_per_active_binding(self):
        tmp = tempfile.TemporaryDirectory(prefix="channel-workflow-multi-")
        try:
            binding_store = ChannelBindingStore(os.path.join(tmp.name, "bridge.db"))
            await binding_store.initialize()
            for index, conversation in enumerate(("chat-1", "chat-2"), start=1):
                await binding_store.create(ChannelBinding(
                    binding_id=f"binding-{index}", channel_instance_id="slack:prod",
                    external_tenant_id="tenant-a", external_conversation_id=conversation,
                    group_id=7, default_bot_id=42, status=BindingStatus.ACTIVE,
                ), allow_active=True)
            group_path = os.path.join(tmp.name, "group.db")
            async with aiosqlite.connect(group_path) as db:
                await initialize_group_channel_outbox(db)
                await db.execute("BEGIN")
                count = await append_workflow_channel_events(db, 7, [{
                    "event_id": "workflow-event-2", "event_type": "workflow_completed",
                    "payload": {"summary": "done"}, "context": {"workflow_id": "wf-2"},
                }], binding_store)
                await db.commit()
                async with db.execute("SELECT event_id,source_event_id FROM group_channel_event_outbox ORDER BY event_id") as cursor:
                    rows = await cursor.fetchall()
            self.assertEqual(count, 2)
            self.assertEqual(len(rows), 2)
            self.assertEqual({row[1] for row in rows}, {"workflow-event-2"})
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
