import os
import tempfile
import unittest

import db as database
import db.writer as db_writer


class TestWorkflowChannelFailureBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_projection_intent_commits_without_reading_channel_database(self):
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
            envelopes = await commit_transition(
                7, "workflow_v1", state={"workflow_id": "wf-7", "current": 0},
                observations=[{
                    "event_type": "workflow_completed",
                    "workflow_id": "wf-7",
                    "payload": {"summary": "done"},
                }],
            )
            self.assertEqual(len(envelopes), 1)
            async with database.connect(path) as db:
                async with db.execute("SELECT state_json FROM workflow_state WHERE group_id=7") as cursor:
                    row = await cursor.fetchone()
                async with db.execute(
                    "SELECT source_event_id,state FROM group_channel_projection_queue"
                ) as cursor:
                    projection = await cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertIn("wf-7", row[0])
            self.assertEqual(projection, (envelopes[0]["event_id"], "pending"))
        finally:
            database.DB_PATH, db_writer.DB_PATH = old_db, old_writer
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
