"""tests/test_agent_orchestrator.py — TaskOrchestrator unit tests.

Tests the coding agent task lifecycle with mocked dependencies.
No real git operations, DB writes, or Supervisor calls are made.
"""
import asyncio
import os
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from integrations.github_client import GitHubIntegrationUnavailable
from plugins.agent_dashboard.api import TaskResponse
from plugins.agent_dashboard.orchestrator import TaskOrchestrator, CODING_AGENT_SYSTEM_PROMPT
from plugins.agent_dashboard.progress import ProgressAdapter
from plugins.agent_dashboard.task_store import (
    IdempotencyConflict,
    TaskStateProjector,
    TaskStore,
)


class DatabaseTestBase(unittest.IsolatedAsyncioTestCase):
    """Base class for tests that need an isolated test database."""

    async def asyncSetUp(self):
        """Set up isolated test database with migrations."""
        import tempfile
        from pathlib import Path
        import db as _database
        import db.writer as _db_writer

        self._tmp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self._tmp.name).resolve()

        # Pin the DB path globals to an isolated temp DB and build its full schema
        self._db_file = str(self.workspace_root / "test_agent_orch.db")
        self._orig_db_paths = (_database.DB_PATH, _db_writer.DB_PATH)
        _database.DB_PATH = self._db_file
        _db_writer.DB_PATH = self._db_file
        await _database.init_db()

    async def asyncTearDown(self):
        """Clean up test database."""
        import db as _database
        import db.writer as _db_writer

        _database.DB_PATH, _db_writer.DB_PATH = self._orig_db_paths
        self._tmp.cleanup()


class TestResolveProvider(unittest.TestCase):

    def test_openai_models(self):
        self.assertEqual(TaskOrchestrator._resolve_provider("gpt-4"), "openai")
        self.assertEqual(TaskOrchestrator._resolve_provider("o1-preview"), "openai")
        self.assertEqual(TaskOrchestrator._resolve_provider("o3-mini"), "openai")

    def test_anthropic_models(self):
        self.assertEqual(TaskOrchestrator._resolve_provider("claude-3-opus"), "anthropic")
        self.assertEqual(TaskOrchestrator._resolve_provider("claude-sonnet-4"), "anthropic")

    def test_deepseek_models(self):
        self.assertEqual(TaskOrchestrator._resolve_provider("deepseek-chat"), "deepseek")
        self.assertEqual(TaskOrchestrator._resolve_provider("deepseek-coder"), "deepseek")

    def test_unknown_defaults_to_deepseek(self):
        self.assertEqual(TaskOrchestrator._resolve_provider("unknown-model"), "deepseek")


class TestCreateTask(DatabaseTestBase):

    async def test_create_task_full_flow(self):
        """create_task creates group, adds bot, clones repo, dispatches agent."""
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)

        # Pre-create group and bot in database (mocked methods return these IDs)
        import db
        async with db.write_connect() as conn:
            await conn.execute(
                "INSERT INTO groups (id, name) VALUES (42, 'test-group')"
            )
            await conn.execute(
                "INSERT INTO members (id, group_id, name, type, role) VALUES (7, 42, 'test-bot', 'bot', 'developer')"
            )
            await conn.commit()

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock) as mock_preflight, \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=42) as mock_group, \
             patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=7) as mock_bot, \
             patch.object(orch, "_clone_repo", new_callable=AsyncMock) as mock_clone, \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock) as mock_dispatch:

            result = await orch.create_task(
                repo_url="https://github.com/user/repo.git",
                requirements="Add login feature",
                base_branch="main",
                test_command="pytest -x",
                model="deepseek-chat",
            )

        self.assertEqual(result["group_id"], 42)
        self.assertEqual(result["bot_id"], 7)
        self.assertEqual(result["status"], "dispatched")
        self.assertTrue(result["task_id"].startswith("agent_"))
        # UUID-based: agent_ + 12 hex chars
        self.assertEqual(len(result["task_id"]), len("agent_") + 12)
        mock_preflight.assert_called_once_with("https://github.com/user/repo.git", "main")
        mock_group.assert_called_once()
        mock_bot.assert_called_once_with(42, "deepseek-chat", 100)
        mock_clone.assert_called_once()
        mock_dispatch.assert_called_once()
        adapter.register_task.assert_called_once_with(42, result["task_id"])

    async def test_create_task_stored_in_registry(self):
        orch = TaskOrchestrator()

        # Pre-create group and bot in database (mocked methods return these IDs)
        import db
        async with db.write_connect() as conn:
            await conn.execute(
                "INSERT INTO groups (id, name) VALUES (1, 'test-group')"
            )
            await conn.execute(
                "INSERT INTO members (id, group_id, name, type, role) VALUES (2, 1, 'test-bot', 'bot', 'developer')"
            )
            await conn.commit()

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock), \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=1), \
             patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=2), \
             patch.object(orch, "_clone_repo", new_callable=AsyncMock), \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock):
            result = await orch.create_task(
                repo_url="https://github.com/x/y.git",
                requirements="Fix bug",
            )

        # Verify task is stored in database
        stored_task = await orch._task_store.get_task(result["task_id"])
        self.assertIsNotNone(stored_task)
        self.assertEqual(stored_task["requirements"], "Fix bug")
        self.assertRegex(stored_task["created_at"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        response = TaskResponse(**stored_task)
        self.assertIsNotNone(response.created_at.tzinfo)

    async def test_same_idempotency_key_returns_original_task(self):
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)

        import db
        async with db.write_connect() as conn:
            await conn.execute("INSERT INTO groups (id, name) VALUES (42, 'test-group')")
            await conn.execute(
                "INSERT INTO members (id, group_id, name, type, role) "
                "VALUES (7, 42, 'test-bot', 'bot', 'developer')"
            )
            await conn.commit()

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock) as preflight, \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=42) as create_group, \
             patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=7) as add_bot, \
             patch.object(orch, "_clone_repo", new_callable=AsyncMock) as clone, \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock) as dispatch:
            first = await orch.create_task(
                repo_url="https://github.com/user/repo.git",
                requirements="Implement durable task creation",
                idempotency_key="request-123",
            )
            second = await orch.create_task(
                repo_url="https://github.com/user/repo.git",
                requirements="Implement durable task creation",
                idempotency_key="request-123",
            )

        self.assertEqual(second["task_id"], first["task_id"])
        preflight.assert_awaited_once()
        create_group.assert_awaited_once()
        add_bot.assert_awaited_once()
        clone.assert_awaited_once()
        dispatch.assert_awaited_once()
        adapter.register_task.assert_called_once()

    async def test_idempotency_key_rejects_different_request(self):
        orch = TaskOrchestrator()
        reservation = await orch._task_store.reserve_request(
            "request-123", "original-hash", "agent_original"
        )
        self.assertTrue(reservation["owner"])

        with patch("plugins.agent_dashboard.orchestrator.hashlib.sha256") as digest:
            digest.return_value.hexdigest.return_value = "different-hash"
            with self.assertRaises(IdempotencyConflict):
                await orch.create_task(
                    repo_url="https://github.com/user/repo.git",
                    requirements="A different task request",
                    idempotency_key="request-123",
                )


class TestTaskStoreIdempotency(DatabaseTestBase):

    async def test_concurrent_reservation_has_one_owner(self):
        store = TaskStore()
        results = await asyncio.gather(
            store.reserve_request("request-123", "same-hash", "agent_one"),
            store.reserve_request("request-123", "same-hash", "agent_two"),
        )

        self.assertEqual(sum(result["owner"] for result in results), 1)
        self.assertEqual({result["task_id"] for result in results}, {results[0]["task_id"]})

    async def test_pending_reservation_self_heals_after_dispatch(self):
        store = TaskStore()
        await store.reserve_request("request-123", "same-hash", "agent_one")

        import db
        async with db.write_connect() as conn:
            await conn.execute("INSERT INTO groups (id, name) VALUES (1, 'test-group')")
            await conn.execute(
                "INSERT INTO members (id, group_id, name, type, role) "
                "VALUES (2, 1, 'test-bot', 'bot', 'developer')"
            )
            await conn.commit()
        await store.create_task(
            task_id="agent_one",
            group_id=1,
            bot_id=2,
            repo_url="https://github.com/user/repo.git",
            requirements="Implement durable task creation",
        )
        await store.update_status("agent_one", "dispatched")

        replay = await store.reserve_request(
            "request-123", "same-hash", "agent_unused"
        )
        self.assertFalse(replay["owner"])
        self.assertEqual(replay["state"], "completed")
        self.assertEqual(replay["task_id"], "agent_one")


class TestTaskStateProjection(DatabaseTestBase):

    async def test_progress_events_persist_terminal_state_and_pr_url(self):
        store = TaskStore()
        import db
        async with db.write_connect() as conn:
            await conn.execute("INSERT INTO groups (id, name) VALUES (1, 'test-group')")
            await conn.execute(
                "INSERT INTO members (id, group_id, name, type, role) "
                "VALUES (2, 1, 'test-bot', 'bot', 'developer')"
            )
            await conn.commit()
        await store.create_task(
            task_id="agent_one",
            group_id=1,
            bot_id=2,
            repo_url="https://github.com/user/repo.git",
            requirements="Implement durable lifecycle projection",
        )
        await store.update_status("agent_one", "dispatched")

        projector = TaskStateProjector(store)
        adapter = ProgressAdapter(projector=projector)
        adapter.register_task(1, "agent_one")
        consumer = asyncio.create_task(projector.run())
        try:
            adapter.on_event(
                1,
                {
                    "type": "tool_result",
                    "tool_name": "create_pr",
                    "result": (
                        "Created https://github.com/user/repo/pull/42，"
                        "ready for review"
                    ),
                },
            )
            adapter.on_event(1, {"type": "workflow_update", "done": True})
            await projector.flush()

            record = await store.get_task("agent_one")
            self.assertEqual(record["status"], "completed")
            self.assertEqual(
                record["pr_url"], "https://github.com/user/repo/pull/42"
            )

            # A delayed activity event cannot regress an already completed task.
            projector.enqueue_status("agent_one", "running")
            await projector.flush()
            record = await store.get_task("agent_one")
            self.assertEqual(record["status"], "completed")
        finally:
            consumer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await consumer


class TestTaskStateProjectorRetry(unittest.IsolatedAsyncioTestCase):

    async def test_transient_write_failure_is_retried(self):
        store = MagicMock()
        store.update_status = AsyncMock(
            side_effect=[RuntimeError("database busy"), True]
        )
        projector = TaskStateProjector(store)
        consumer = asyncio.create_task(projector.run())
        try:
            projector.enqueue_status("agent_one", "completed")
            await asyncio.wait_for(projector.flush(), timeout=1)
        finally:
            consumer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await consumer

        self.assertEqual(store.update_status.await_count, 2)
        self.assertNotIn("agent_one", projector._last_status)


class TestRetryTask(DatabaseTestBase):

    async def test_retry_known_task(self):
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)

        # Pre-create group and bot in database
        import db
        async with db.write_connect() as conn:
            await conn.execute(
                "INSERT INTO groups (id, name) VALUES (10, 'test-group')"
            )
            await conn.execute(
                "INSERT INTO members (id, group_id, name, type, role) VALUES (5, 10, 'test-bot', 'bot', 'developer')"
            )
            await conn.commit()

        # Create test data in database
        await orch._task_store.create_task(
            task_id="task_1",
            group_id=10,
            bot_id=5,
            repo_url="https://github.com/test/repo.git",
            requirements="Add feature",
            base_branch="main",
            test_command="pytest",
            model="deepseek-chat",
            max_iterations=100,
        )
        await orch._task_store.update_status("task_1", "stuck")

        with patch.object(orch, "_send_abort", new_callable=AsyncMock) as mock_abort, \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock) as mock_dispatch:

            result = await orch.retry_task("task_1")

        self.assertEqual(result["status"], "restarted")
        mock_abort.assert_called_once_with(10, "task_1", mode="retry")
        mock_dispatch.assert_called_once_with(10, 5, "Add feature", "pytest", task_id="task_1")
        adapter.reset_for_retry.assert_called_once_with(10)

    async def test_retry_unknown_task_raises(self):
        orch = TaskOrchestrator()
        with self.assertRaises(ValueError) as ctx:
            await orch.retry_task("nonexistent")
        self.assertIn("not found", str(ctx.exception))


class TestAbortTask(DatabaseTestBase):

    async def test_abort_known_task(self):
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)

        # Pre-create group and bot in database
        import db
        async with db.write_connect() as conn:
            await conn.execute(
                "INSERT INTO groups (id, name) VALUES (10, 'test-group')"
            )
            await conn.execute(
                "INSERT INTO members (id, group_id, name, type, role) VALUES (5, 10, 'test-bot', 'bot', 'developer')"
            )
            await conn.commit()

        # Create test data in database
        await orch._task_store.create_task(
            task_id="task_1",
            group_id=10,
            bot_id=5,
            repo_url="https://github.com/test/repo.git",
            requirements="Test task",
            base_branch="main",
            test_command="pytest",
            model="deepseek-chat",
            max_iterations=100,
        )
        await orch._task_store.update_status("task_1", "running")

        with patch.object(orch, "_send_abort", new_callable=AsyncMock) as mock_abort:

            result = await orch.abort_task("task_1")

        self.assertEqual(result["status"], "aborted")
        mock_abort.assert_called_once_with(10, "task_1", mode="abort")
        adapter.mark_aborted.assert_called_once_with(10)

    async def test_abort_unknown_task_raises(self):
        orch = TaskOrchestrator()
        with self.assertRaises(ValueError):
            await orch.abort_task("nonexistent")


class TestSendAbort(DatabaseTestBase):

    async def test_send_abort_via_ipc(self):
        """_send_abort calls Supervisor.request_abort() and returns ACK."""
        mock_sup = MagicMock()
        mock_sup.request_abort = AsyncMock(return_value={
            "task_id": "task_1",
            "mode": "abort",
            "cancelled_count": 2,
            "cleanup_status": "success",
            "workspace_action": "discard",
            "workspace_cleaned": True,
            "error": None,
        })

        from runtime import supervisor as sup_module
        original_sup = sup_module.supervisor
        sup_module.supervisor = mock_sup

        try:
            orch = TaskOrchestrator()
            ack = await orch._send_abort(42, "task_1", mode="abort")
        finally:
            sup_module.supervisor = original_sup

        # Verify request_abort was called with correct parameters
        mock_sup.request_abort.assert_called_once_with(
            42, task_id="task_1", mode="abort", timeout=10.0
        )

        # Verify ACK is returned
        self.assertEqual(ack["cancelled_count"], 2)
        self.assertEqual(ack["cleanup_status"], "success")

    async def test_send_abort_no_supervisor(self):
        """_send_abort raises RuntimeError when supervisor is not available (fail closed)."""
        from runtime import supervisor as sup_module
        original_sup = sup_module.supervisor
        sup_module.supervisor = None

        try:
            orch = TaskOrchestrator()
            with self.assertRaises(RuntimeError) as ctx:
                await orch._send_abort(42, "task_1")
            self.assertIn("Supervisor not available", str(ctx.exception))
        finally:
            sup_module.supervisor = original_sup


class TestCreateGroup(DatabaseTestBase):

    async def test_creates_group_in_db(self):
        orch = TaskOrchestrator()

        mock_cursor = AsyncMock()
        mock_cursor.lastrowid = 99
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock(return_value=False)

        mock_db = AsyncMock()
        mock_db.execute = MagicMock(return_value=mock_cursor)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch("db.write_connect", return_value=mock_db), \
             patch("workspace.init_group_workspace", new_callable=AsyncMock) as mock_init:

            group_id = await orch._create_group("agent_123")

        self.assertEqual(group_id, 99)
        mock_init.assert_called_once_with(99, "Coding Agent: agent_123")


class TestDispatchAgent(DatabaseTestBase):

    async def test_dispatches_via_supervisor(self):
        orch = TaskOrchestrator()

        mock_sup = MagicMock()
        mock_sup.send_to_worker = AsyncMock()

        # Patch the module-level 'supervisor' variable that _dispatch_agent reads
        from runtime import supervisor as sup_module
        original_sup = sup_module.supervisor
        sup_module.supervisor = mock_sup

        mock_envelope = MagicMock(return_value={"type": "start_workflow"})
        with patch.object(sup_module.ipc.protocol, "envelope", mock_envelope):
            try:
                await orch._dispatch_agent(
                    group_id=10,
                    bot_id=5,
                    requirements="Build a REST API",
                    test_command="pytest -x",
                )
            finally:
                sup_module.supervisor = original_sup

        mock_sup.send_to_worker.assert_called_once()
        # Verify START_WORKFLOW is used (not USER_MESSAGE)
        call_args = mock_sup.send_to_worker.call_args
        self.assertEqual(call_args[0][0], 10)  # group_id


class TestSystemPrompt(unittest.TestCase):

    def test_prompt_contains_workflow_steps(self):
        self.assertIn("Explore", CODING_AGENT_SYSTEM_PROMPT)
        self.assertIn("Implement", CODING_AGENT_SYSTEM_PROMPT)
        self.assertIn("Test", CODING_AGENT_SYSTEM_PROMPT)
        self.assertIn("Commit", CODING_AGENT_SYSTEM_PROMPT)
        self.assertIn("PR", CODING_AGENT_SYSTEM_PROMPT)

    def test_prompt_contains_test_loop(self):
        self.assertIn("test", CODING_AGENT_SYSTEM_PROMPT.lower())
        self.assertIn("fix", CODING_AGENT_SYSTEM_PROMPT.lower())
        self.assertIn("iterate", CODING_AGENT_SYSTEM_PROMPT.lower())

    def test_prompt_contains_rules(self):
        self.assertIn("NEVER skip testing", CODING_AGENT_SYSTEM_PROMPT)
        self.assertIn("edit_file", CODING_AGENT_SYSTEM_PROMPT)


class TestPreflightCheck(DatabaseTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._github_env = patch.dict(
            os.environ,
            {"NUKE_GITHUB_ENABLED": "true", "GITHUB_TOKEN": "test-token"},
        )
        self._github_env.start()
        self._gh = patch("shutil.which", return_value="/usr/bin/gh")
        self._gh.start()

    async def asyncTearDown(self):
        self._gh.stop()
        self._github_env.stop()
        await super().asyncTearDown()

    async def test_preflight_success(self):
        """Valid repo passes pre-flight check."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"abc123\trefs/heads/main\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            orch = TaskOrchestrator()
            await orch._preflight_check_repo("https://github.com/user/repo.git", "main")

    async def test_preflight_repo_not_found(self):
        """Invalid repo URL raises RuntimeError."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: repository not found"))
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            orch = TaskOrchestrator()
            with self.assertRaises(RuntimeError) as ctx:
                await orch._preflight_check_repo("https://github.com/user/nonexistent.git")
            self.assertIn("not reachable", str(ctx.exception))

    async def test_preflight_timeout(self):
        """Slow repo server triggers timeout."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            orch = TaskOrchestrator()
            with self.assertRaises(RuntimeError) as ctx:
                await orch._preflight_check_repo("https://slow-server/repo.git")
            self.assertIn("timed out", str(ctx.exception))

    async def test_preflight_branch_not_found(self):
        """Non-existent branch raises RuntimeError."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 2

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            orch = TaskOrchestrator()
            with self.assertRaises(RuntimeError) as ctx:
                await orch._preflight_check_repo("https://github.com/user/repo.git", "nonexistent")
            self.assertIn("not found", str(ctx.exception))


class TestRollback(DatabaseTestBase):

    async def test_rollback_none_group(self):
        """Rollback with None group_id is a no-op."""
        orch = TaskOrchestrator()
        await orch._rollback_group(None)  # should not raise

    async def test_rollback_cleans_workspace(self):
        """Rollback removes workspace directory."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            from pathlib import Path
            group_dir = Path(tmp_dir) / "group_42"
            group_dir.mkdir()

            with patch("workspace.layout.group_dir", return_value=group_dir), \
                 patch("runtime.dbpaths.group_db_path", return_value="/nonexistent"), \
                 patch("db.write_connect") as mock_wc:

                mock_db = AsyncMock()
                mock_db.execute = MagicMock(return_value=AsyncMock())
                mock_db.commit = AsyncMock()
                mock_db.__aenter__ = AsyncMock(return_value=mock_db)
                mock_db.__aexit__ = AsyncMock(return_value=False)
                mock_wc.return_value = mock_db

                orch = TaskOrchestrator()
                await orch._rollback_group(42)

            self.assertFalse(group_dir.exists())

    async def test_rollback_cleans_db(self):
        """Rollback deletes group and member rows from central DB."""
        mock_db = AsyncMock()
        # db.execute() is awaited directly (no context manager in _rollback_group)
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        # write_connect() is an async context manager
        mock_wc = AsyncMock()
        mock_wc.__aenter__ = AsyncMock(return_value=mock_db)
        mock_wc.__aexit__ = AsyncMock(return_value=False)

        with patch("workspace.layout.group_dir") as mock_gd, \
             patch("runtime.dbpaths.group_db_path", return_value="/nonexistent"), \
             patch("db.write_connect", return_value=mock_wc):

            from pathlib import Path
            mock_gd.return_value = Path("/nonexistent_dir")

            orch = TaskOrchestrator()
            await orch._rollback_group(42)

        # Verify DELETE statements were called
        calls = [str(c) for c in mock_db.execute.call_args_list]
        self.assertTrue(any("DELETE FROM members" in c for c in calls))
        self.assertTrue(any("DELETE FROM groups" in c for c in calls))


class TestCreateTaskAtomicity(DatabaseTestBase):

    async def _setup_test_group_and_bot(self, group_id=42, bot_id=7):
        """Helper to create test group and bot in database."""
        import db
        async with db.write_connect() as conn:
            await conn.execute(
                f"INSERT INTO groups (id, name) VALUES ({group_id}, 'test-group')"
            )
            await conn.execute(
                f"INSERT INTO members (id, group_id, name, type, role) VALUES ({bot_id}, {group_id}, 'test-bot', 'bot', 'developer')"
            )
            await conn.commit()

    async def test_clone_failure_rolls_back_group(self):
        """If clone fails, group is rolled back (no orphan resources)."""
        orch = TaskOrchestrator()
        await self._setup_test_group_and_bot()

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock), \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=42), \
             patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=7), \
             patch.object(orch, "_clone_repo", new_callable=AsyncMock,
                          side_effect=RuntimeError("clone failed")), \
             patch.object(orch, "_rollback_group", new_callable=AsyncMock) as mock_rollback:

            with self.assertRaises(RuntimeError) as ctx:
                await orch.create_task(
                    repo_url="https://github.com/user/repo.git",
                    requirements="test",
                )
            self.assertIn("clone failed", str(ctx.exception))
            mock_rollback.assert_called_once_with(42)

    async def test_preflight_failure_no_rollback_needed(self):
        """If pre-flight fails, no group was created so no rollback."""
        orch = TaskOrchestrator()

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock,
                          side_effect=RuntimeError("repo not found")), \
             patch.object(orch, "_create_group", new_callable=AsyncMock) as mock_create, \
             patch.object(orch, "_rollback_group", new_callable=AsyncMock) as mock_rollback:

            with self.assertRaises(RuntimeError):
                await orch.create_task(
                    repo_url="https://github.com/user/nonexistent.git",
                    requirements="test",
                )
            mock_create.assert_not_called()
            mock_rollback.assert_not_called()

    async def test_github_unavailable_preserves_typed_failure(self):
        orch = TaskOrchestrator()

        with patch.object(
            orch,
            "_preflight_check_repo",
            new_callable=AsyncMock,
            side_effect=GitHubIntegrationUnavailable("GitHub integration is disabled"),
        ), patch.object(orch, "_create_group", new_callable=AsyncMock) as create_group:
            with self.assertRaises(GitHubIntegrationUnavailable):
                await orch.create_task(
                    repo_url="https://github.com/user/repo.git",
                    requirements="Implement durable task creation",
                    idempotency_key="request-123",
                )

        create_group.assert_not_awaited()

    async def test_add_bot_failure_rolls_back_group(self):
        """If bot creation fails, group is rolled back."""
        orch = TaskOrchestrator()
        await self._setup_test_group_and_bot()

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock), \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=42), \
             patch.object(orch, "_add_bot", new_callable=AsyncMock,
                          side_effect=RuntimeError("bot creation failed")), \
             patch.object(orch, "_rollback_group", new_callable=AsyncMock) as mock_rollback:

            with self.assertRaises(RuntimeError):
                await orch.create_task(
                    repo_url="https://github.com/user/repo.git",
                    requirements="test",
                )
            mock_rollback.assert_called_once_with(42)

    async def test_success_no_rollback(self):
        """Successful create_task does NOT trigger rollback."""
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)
        await self._setup_test_group_and_bot()

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock), \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=42), \
             patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=7), \
             patch.object(orch, "_clone_repo", new_callable=AsyncMock), \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock), \
             patch.object(orch, "_rollback_group", new_callable=AsyncMock) as mock_rollback:

            result = await orch.create_task(
                repo_url="https://github.com/user/repo.git",
                requirements="test",
            )
            mock_rollback.assert_not_called()
            self.assertEqual(result["status"], "dispatched")

    async def test_worker_id_binds_before_dispatch(self):
        """When worker_id is provided, group is bound to worker before dispatch."""
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)
        await self._setup_test_group_and_bot()

        call_order = []

        async def track_bind(group_id, worker_id):
            call_order.append("bind")

        async def track_dispatch(group_id, bot_id, req, cmd, task_id=""):
            call_order.append("dispatch")

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock), \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=42), \
             patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=7), \
             patch.object(orch, "_clone_repo", new_callable=AsyncMock), \
             patch.object(orch, "_bind_group_to_worker", new_callable=AsyncMock,
                          side_effect=track_bind) as mock_bind, \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock,
                          side_effect=track_dispatch):

            await orch.create_task(
                repo_url="https://github.com/user/repo.git",
                requirements="test",
                worker_id="w2",
            )

        # Bind must happen BEFORE dispatch
        mock_bind.assert_called_once_with(42, "w2")
        self.assertEqual(call_order, ["bind", "dispatch"])

    async def test_no_worker_id_skips_bind(self):
        """When worker_id is None, bind is skipped (uses default modulo routing)."""
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)
        await self._setup_test_group_and_bot()

        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock), \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=42), \
             patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=7), \
             patch.object(orch, "_clone_repo", new_callable=AsyncMock), \
             patch.object(orch, "_bind_group_to_worker", new_callable=AsyncMock) as mock_bind, \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock):

            await orch.create_task(
                repo_url="https://github.com/user/repo.git",
                requirements="test",
                worker_id=None,
            )

        mock_bind.assert_not_called()

    async def test_task_ids_no_collision_under_concurrency(self):
        """Concurrent create_task calls produce unique task IDs (UUID-based)."""
        orch = TaskOrchestrator()
        await self._setup_test_group_and_bot(group_id=1, bot_id=1)
        ids = set()

        for _ in range(50):
            with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock), \
                 patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=1), \
                 patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=1), \
                 patch.object(orch, "_clone_repo", new_callable=AsyncMock), \
                 patch.object(orch, "_dispatch_agent", new_callable=AsyncMock):

                result = await orch.create_task(
                    repo_url="https://github.com/user/repo.git",
                    requirements="test",
                )
                ids.add(result["task_id"])

        self.assertEqual(len(ids), 50)  # All unique


class TestBindGroupToWorker(DatabaseTestBase):

    async def test_binds_group_in_db(self):
        """_bind_group_to_worker updates assigned_worker_id in central DB."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_wc = AsyncMock()
        mock_wc.__aenter__ = AsyncMock(return_value=mock_db)
        mock_wc.__aexit__ = AsyncMock(return_value=False)

        with patch("db.write_connect", return_value=mock_wc):
            orch = TaskOrchestrator()
            await orch._bind_group_to_worker(42, "w3")

        # Verify the UPDATE statement was called
        mock_db.execute.assert_called_once()
        args = mock_db.execute.call_args[0]
        self.assertIn("UPDATE groups SET assigned_worker_id", args[0])
        self.assertEqual(args[1], ("w3", 42))
