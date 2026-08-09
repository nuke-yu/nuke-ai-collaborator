import os
import tempfile
import unittest

import aiosqlite

from channels.bridge import (
    BindingStatus,
    ChannelBinding,
    ChannelBindingStore,
    WorkflowChannelProjectionRelay,
    WorkflowProjectionResult,
    enqueue_workflow_channel_projections,
)


class TestWorkflowChannelProjectionRelay(unittest.IsolatedAsyncioTestCase):
    async def test_transient_binding_failure_is_replayed_atomically(self):
        with tempfile.TemporaryDirectory(prefix="channel-projection-") as directory:
            group_path = os.path.join(directory, "group.db")
            channel_path = os.path.join(directory, "channel.db")
            observation = {
                "event_id": "source-1",
                "event_type": "workflow_completed",
                "trace_id": "trace-1",
                "context": {"workflow_id": "wf-1", "session_id": "session-1"},
                "payload": {"summary": "done"},
            }
            async with aiosqlite.connect(group_path) as conn:
                await conn.execute("BEGIN")
                await enqueue_workflow_channel_projections(conn, 7, [observation])
                await conn.commit()

            missing_store = ChannelBindingStore(os.path.join(directory, "missing", "channel.db"))
            relay = WorkflowChannelProjectionRelay(group_path, missing_store, retry_delay_ms=0)
            self.assertEqual(
                await relay.run_once(7, now_ms=10**15),
                WorkflowProjectionResult.RETRY_SCHEDULED,
            )

            store = ChannelBindingStore(channel_path)
            await store.initialize()
            for suffix in ("a", "b"):
                binding = ChannelBinding(
                    binding_id=f"binding-{suffix}", channel_instance_id="slack:prod",
                    external_tenant_id="tenant", external_conversation_id=f"chat-{suffix}",
                    group_id=7, default_bot_id=1, status=BindingStatus.CONFIGURED,
                )
                await store.create(binding)
                await store.transition(binding.binding_id, BindingStatus.PENDING_APPROVAL)
                await store.transition(binding.binding_id, BindingStatus.ACTIVE)
            relay = WorkflowChannelProjectionRelay(group_path, store, retry_delay_ms=0)
            self.assertEqual(
                await relay.run_once(7, now_ms=10**15 + 1),
                WorkflowProjectionResult.PROJECTED,
            )
            async with aiosqlite.connect(group_path) as conn:
                async with conn.execute("SELECT COUNT(*) FROM group_channel_event_outbox") as cursor:
                    self.assertEqual((await cursor.fetchone())[0], 2)
                async with conn.execute("SELECT state,attempts FROM group_channel_projection_queue") as cursor:
                    self.assertEqual(await cursor.fetchone(), ("projected", 2))


if __name__ == "__main__":
    unittest.main()
