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
            ))
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
                async with db.execute("SELECT state,payload_json FROM group_channel_event_outbox WHERE event_id=?", ("workflow-event-1",)) as cursor:
                    row = await cursor.fetchone()
            self.assertEqual(row[0], "pending")
            self.assertIn("workflow_id", row[1])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
