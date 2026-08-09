"""Unit and API integration tests for Session Execution Timeline Projector."""

import unittest
import os
import shutil
import tempfile
import db as _db
from unittest.mock import patch
from db.schema_split import init_group_db
from runtime.dbpaths import group_db_path
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
        self.workspace_patcher = patch("skills.constants.WORKSPACE_ROOT", self.tmp_dir)
        self.workspace_patcher.start()

    async def asyncTearDown(self):
        self.workspace_patcher.stop()
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
        self.assertIn("调取 4 条关联记忆", node1.detail)
        self.assertIn("code_review", node1.detail)

        # 2. Tool Execution
        node2 = project_event_to_node(
            {
                "event_type": "tool_execution",
                "payload": {
                    "tool_name": "write_file",
                    "duration_ms": 450,
                    "summary": "Modified Login.jsx",
                    "artifact_ids": ["art_123"],
                    "arguments": {"path": "src/Login.jsx", "content": "..."},
                    "stdout": "[written] src/Login.jsx",
                },
                "occurred_at": 1785689100,
                "event_id": "evt_2",
            },
            idx=2,
        )
        self.assertIsNotNone(node2)
        self.assertEqual(node2.type, "tool_execution")
        self.assertEqual(node2.title, "执行工具: write_file")
        self.assertEqual(node2.duration_s, 0.45)
        self.assertEqual(node2.artifact_ids, ["art_123"])
        self.assertEqual(node2.metadata["arguments"]["path"], "src/Login.jsx")
        self.assertEqual(node2.metadata["result"], "[written] src/Login.jsx")
        self.assertFalse(node2.metadata["is_error"])

        # 3. Thinking event
        node_thinking = project_event_to_node(
            {
                "event_type": "thinking",
                "payload": {"content": "先检查现有路由，再修改登录组件。"},
                "occurred_at": 1785689150,
                "event_id": "evt_thinking",
            },
            idx=3,
        )
        self.assertIsNotNone(node_thinking)
        self.assertEqual(node_thinking.type, "thinking")
        self.assertEqual(node_thinking.detail, "先检查现有路由，再修改登录组件。")

        # 4. Failed tool execution keeps output, duration, and error status
        node_failed = project_event_to_node(
            {
                "event_type": "tool_result",
                "payload": {
                    "tool": "run_shell",
                    "duration_ms": 1200,
                    "args": {"command": "pytest -q"},
                    "output": "2 failed",
                    "is_error": True,
                    "artifact_ids": "art_failure_log",
                },
                "occurred_at": 1785689180,
                "event_id": "evt_failed",
            },
            idx=4,
        )
        self.assertIsNotNone(node_failed)
        self.assertEqual(node_failed.status, "failed")
        self.assertEqual(node_failed.duration_s, 1.2)
        self.assertEqual(node_failed.artifact_ids, ["art_failure_log"])
        self.assertEqual(node_failed.metadata["result"], "2 failed")
        self.assertTrue(node_failed.metadata["is_error"])

        node_secret = project_event_to_node(
            {
                "event_type": "tool_result",
                "payload": {
                    "tool_name": "run_shell",
                    "result": "Authorization: Bearer " + "a" * 48 + "\n" + "x" * 5000,
                },
                "occurred_at": 1785689181,
                "event_id": "evt_secret",
            },
            idx=5,
        )
        self.assertNotIn("a" * 48, node_secret.metadata["result"])
        self.assertLessEqual(len(node_secret.metadata["result"]), 4000)

        # 5. Permission Approval
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
        group_path = group_db_path(group_id)
        os.makedirs(os.path.dirname(group_path), exist_ok=True)
        await init_group_db(group_path)

        with _db.bind_db(group_path):
            # Session ownership is group-local. Central group/member metadata is
            # not needed by the projector, so keep this fixture on the split DB
            # boundary used by production code.
            async with _db.connect() as conn:
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
