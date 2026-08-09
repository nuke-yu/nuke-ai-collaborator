import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import db as database
import db.writer as db_writer


class TestWorkflowChannelFailureBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_failure_does_not_rollback_workflow_transition(self):
        tmp = tempfile.TemporaryDirectory(prefix="workflow-channel-failure-")
        old_db, old_writer = database.DB_PATH, db_writer.DB_PATH
        try:
            path = os.path.join(tmp.name, "group.db")
            database.DB_PATH = path
            db_writer.DB_PATH = path
            await database.init_db()
            async with database.connect(path) as db:
                await db.execute("INSERT INTO groups(id,name) VALUES(7,'Failure Boundary')")
                await db.commit()
            from core.workflow_store import commit_transition
            with patch("channels.bridge.append_workflow_channel_events", new=AsyncMock(side_effect=OSError("bridge unavailable"))):
                envelopes = await commit_transition(
                    7, "workflow_v1", state={"workflow_id": "wf-7", "current": 0},
                    observations=[],
                )
            self.assertEqual(envelopes, [])
            async with database.connect(path) as db:
                async with db.execute("SELECT state_json FROM workflow_state WHERE group_id=7") as cursor:
                    row = await cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertIn("wf-7", row[0])
        finally:
            database.DB_PATH, db_writer.DB_PATH = old_db, old_writer
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
