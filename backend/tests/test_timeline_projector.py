"""Unit and API integration tests for Session Execution Timeline Projector."""

import unittest
import os
import shutil
import tempfile
import db as _db
from sessions.store import append_event
from observability.timeline_projector import (
    TimelineNode,
    ExecutionTimelineProjection,
    project_event_to_node,
    project_session_timeline,
)


class TestTimelineProjector(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_timeline.db")
        _db.DB_PATH = self.db_path
        await _db.init_db()

    async def asyncTearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_project_event_to_node(self):
        # 1. Context Injected
        node1 = project_event_to_node(
            {
                "event_type": "context_injected",
                "payload": {"facts_count": 4, "skills": ["code_review", "test_runner"]},
                "occurred_at": 1785689000,
                "event_id": "evt_1",
            },
            idx=1,
        )
        self.assertIsNotNone(node1)
        self.assertEqual(node1.type, "context_injected")
        self.assertIn("4 memory facts", node1.detail)
        self.assertIn("code_review", node1.detail)

        # 2. Tool Execution
        node2 = project_event_to_node(
            {
                "event_type": "tool_execution",
                "payload": {
                    "tool_name": "write_file",
                    "duration_s": 0.45,
                    "summary": "Modified Login.jsx",
                    "artifact_ids": ["art_123"],
                },
                "occurred_at": 1785689100,
                "event_id": "evt_2",
            },
            idx=2,
        )
        self.assertIsNotNone(node2)
        self.assertEqual(node2.type, "tool_execution")
        self.assertEqual(node2.title, "Executed Tool: write_file")
        self.assertEqual(node2.duration_s, 0.45)
        self.assertEqual(node2.artifact_ids, ["art_123"])

        # 3. Permission Approval
        node3 = project_event_to_node(
            {
                "event_type": "permission_approved",
                "payload": {"action": "allow", "tool_pattern": "run_shell"},
                "actor": {"type": "human"},
                "occurred_at": 1785689200,
                "event_id": "evt_3",
            },
            idx=3,
        )
        self.assertIsNotNone(node3)
        self.assertEqual(node3.type, "permission_approved")
        self.assertIn("ALLOW", node3.title)

    async def test_project_session_timeline_flow(self):
        group_id = 1
        session_id = "sess_timeline_test"

        with _db.bind_db(self.db_path):
            # Create group and member in DB
            async with _db.connect() as conn:
                await conn.execute(
                    "INSERT INTO groups (id, name) VALUES (?, ?)", (group_id, "Test Group")
                )
                await conn.execute(
                    "INSERT INTO members (id, group_id, name, type) VALUES (?, ?, ?, ?)",
                    (2, group_id, "DevBot", "bot"),
                )
                await conn.execute(
                    "INSERT INTO agent_sessions (id, group_id, bot_id, status) VALUES (?, ?, ?, ?)",
                    (session_id, group_id, 2, "completed"),
                )
                await conn.commit()

            # Append events to session
            await append_event(
                session_id,
                "session_start",
                {"facts_count": 2, "skills": ["python_dev"]},
            )
            await append_event(
                session_id,
                "tool_execution",
                {
                    "tool_name": "run_shell",
                    "duration_s": 0.82,
                    "summary": "Ran pytest",
                    "artifact_ids": ["art_999"],
                },
            )
            await append_event(
                session_id,
                "deliverable_produced",
                {
                    "display_name": "Test Run Report",
                    "description": "Passed 42 tests cleanly",
                    "artifact_id": "art_999",
                },
            )

            # Generate timeline projection
            projection = await project_session_timeline(group_id=group_id, session_id=session_id)

            self.assertEqual(projection.session_id, session_id)
            self.assertEqual(projection.group_id, group_id)
            self.assertEqual(projection.status, "completed")
            self.assertEqual(len(projection.nodes), 3)

            node_types = [n.type for n in projection.nodes]
            self.assertEqual(node_types, ["context_injected", "tool_execution", "deliverable_produced"])
            self.assertAlmostEqual(projection.total_duration_s, 0.82, places=2)


if __name__ == "__main__":
    unittest.main()
