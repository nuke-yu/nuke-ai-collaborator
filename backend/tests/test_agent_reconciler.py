"""Coding-agent durable lifecycle reconciliation tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import db
from plugins.agent_dashboard.reconciler import TaskReconciler, finalize_group_state


class TestTaskReconciler(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "group.db")
        async with db.write_connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE agent_sessions (
                    id TEXT PRIMARY KEY, bot_id INTEGER, group_id INTEGER,
                    status TEXT, created_at TEXT, updated_at TEXT
                );
                CREATE TABLE workflow_state (group_id INTEGER PRIMARY KEY);
                CREATE TABLE groups (id INTEGER PRIMARY KEY, assigned_worker_id TEXT);
                INSERT INTO groups (id, assigned_worker_id) VALUES (10, 'w0');
                """
            )
            await conn.commit()

    async def asyncTearDown(self):
        await db.aclose_writer(self.db_path)
        self._tmp.cleanup()

    async def test_keeps_newest_live_session_and_supersedes_older_copy(self):
        async with db.write_connect(self.db_path) as conn:
            await conn.executemany(
                """INSERT INTO agent_sessions
                   (id, bot_id, group_id, status, created_at, updated_at)
                   VALUES (?, 5, 10, 'running', ?, ?)""",
                [
                    ("old", "2026-01-01", "2026-01-01"),
                    ("new", "2026-01-02", "2026-01-02"),
                ],
            )
            await conn.commit()
        store = AsyncMock()
        reconciler = TaskReconciler(store)

        with patch(
            "plugins.agent_dashboard.reconciler.group_db_path",
            return_value=self.db_path,
        ):
            await reconciler._reconcile_task(
                {"task_id": "task", "group_id": 10, "bot_id": 5, "status": "running"}
            )

        async with db.connect(self.db_path) as conn:
            cur = await conn.execute("SELECT id, status FROM agent_sessions ORDER BY id")
            self.assertEqual(await cur.fetchall(), [("new", "running"), ("old", "superseded")])
        store.update_status.assert_not_awaited()

    async def test_terminal_task_closes_sessions_and_workflow(self):
        async with db.write_connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO agent_sessions
                   (id, bot_id, group_id, status, created_at, updated_at)
                   VALUES ('session', 5, 10, 'needs_review', datetime('now'), datetime('now'))"""
            )
            await conn.execute("INSERT INTO workflow_state (group_id) VALUES (10)")
            await conn.commit()

        with patch(
            "plugins.agent_dashboard.reconciler.group_db_path",
            return_value=self.db_path,
        ), patch("plugins.agent_dashboard.reconciler.db.DB_PATH", self.db_path):
            await finalize_group_state(10, 5)

        async with db.connect(self.db_path) as conn:
            session = await (await conn.execute(
                "SELECT status FROM agent_sessions WHERE id = 'session'"
            )).fetchone()
            workflow = await (await conn.execute(
                "SELECT COUNT(*) FROM workflow_state"
            )).fetchone()
            assignment = await (await conn.execute(
                "SELECT assigned_worker_id FROM groups WHERE id = 10"
            )).fetchone()
        self.assertEqual(session[0], "aborted")
        self.assertEqual(workflow[0], 0)
        self.assertIsNone(assignment[0])


if __name__ == "__main__":
    unittest.main()
