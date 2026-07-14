"""tests/test_agent_orchestrator.py — TaskOrchestrator unit tests.

Tests the coding agent task lifecycle with mocked dependencies.
No real git operations, DB writes, or Supervisor calls are made.
"""
import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from plugins.agent_dashboard.orchestrator import TaskOrchestrator, CODING_AGENT_SYSTEM_PROMPT


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


class TestCreateTask(unittest.IsolatedAsyncioTestCase):

    async def test_create_task_full_flow(self):
        """create_task creates group, adds bot, clones repo, dispatches agent."""
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)

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
        self.assertIn("agent_", result["task_id"])
        mock_preflight.assert_called_once_with("https://github.com/user/repo.git", "main")
        mock_group.assert_called_once()
        mock_bot.assert_called_once_with(42, "deepseek-chat", 100)
        mock_clone.assert_called_once()
        mock_dispatch.assert_called_once()
        adapter.register_task.assert_called_once_with(42, result["task_id"])

    async def test_create_task_stored_in_registry(self):
        orch = TaskOrchestrator()
        with patch.object(orch, "_preflight_check_repo", new_callable=AsyncMock), \
             patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=1), \
             patch.object(orch, "_add_bot", new_callable=AsyncMock, return_value=2), \
             patch.object(orch, "_clone_repo", new_callable=AsyncMock), \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock):
            result = await orch.create_task(
                repo_url="https://github.com/x/y.git",
                requirements="Fix bug",
            )

        self.assertIn(result["task_id"], orch.tasks)
        self.assertEqual(orch.tasks[result["task_id"]]["requirements"], "Fix bug")


class TestRetryTask(unittest.IsolatedAsyncioTestCase):

    async def test_retry_known_task(self):
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)
        orch._tasks["task_1"] = {
            "task_id": "task_1",
            "group_id": 10,
            "bot_id": 5,
            "requirements": "Add feature",
            "test_command": "pytest",
            "status": "stuck",
        }

        with patch("core.bg.abort_group", return_value=2), \
             patch("workspace.git_worktree.remove_worktree", new_callable=AsyncMock), \
             patch.object(orch, "_dispatch_agent", new_callable=AsyncMock) as mock_dispatch:

            result = await orch.retry_task("task_1")

        self.assertEqual(result["status"], "restarted")
        self.assertIn("restarted_at", result)
        mock_dispatch.assert_called_once_with(10, 5, "Add feature", "pytest")
        adapter.unregister_task.assert_called_once_with(10)
        adapter.register_task.assert_called_once_with(10, "task_1")

    async def test_retry_unknown_task_raises(self):
        orch = TaskOrchestrator()
        with self.assertRaises(ValueError) as ctx:
            await orch.retry_task("nonexistent")
        self.assertIn("not found", str(ctx.exception))


class TestAbortTask(unittest.IsolatedAsyncioTestCase):

    async def test_abort_known_task(self):
        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)
        orch._tasks["task_1"] = {
            "task_id": "task_1",
            "group_id": 10,
            "bot_id": 5,
            "status": "running",
        }

        with patch("core.bg.abort_group", return_value=1), \
             patch("workspace.git_worktree.remove_worktree", new_callable=AsyncMock):

            result = await orch.abort_task("task_1")

        self.assertEqual(result["status"], "aborted")
        adapter.unregister_task.assert_called_once_with(10)

    async def test_abort_unknown_task_raises(self):
        orch = TaskOrchestrator()
        with self.assertRaises(ValueError):
            await orch.abort_task("nonexistent")


class TestCreateGroup(unittest.IsolatedAsyncioTestCase):

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


class TestDispatchAgent(unittest.IsolatedAsyncioTestCase):

    async def test_dispatches_via_supervisor(self):
        orch = TaskOrchestrator()

        mock_sup = MagicMock()
        mock_sup.send_to_worker = AsyncMock()

        # Patch the module-level 'supervisor' variable that _dispatch_agent reads
        from runtime import supervisor as sup_module
        original_sup = sup_module.supervisor
        sup_module.supervisor = mock_sup

        mock_envelope = MagicMock(return_value={"type": "user_message"})
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


class TestPreflightCheck(unittest.IsolatedAsyncioTestCase):

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


class TestRollback(unittest.IsolatedAsyncioTestCase):

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


class TestCreateTaskAtomicity(unittest.IsolatedAsyncioTestCase):

    async def test_clone_failure_rolls_back_group(self):
        """If clone fails, group is rolled back (no orphan resources)."""
        orch = TaskOrchestrator()

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

    async def test_add_bot_failure_rolls_back_group(self):
        """If bot creation fails, group is rolled back."""
        orch = TaskOrchestrator()

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
