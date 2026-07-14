"""tests/test_agent_orchestrator.py — TaskOrchestrator unit tests.

Tests the coding agent task lifecycle with mocked dependencies.
No real git operations, DB writes, or Supervisor calls are made.
"""
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

        with patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=42) as mock_group, \
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
        mock_group.assert_called_once()
        mock_bot.assert_called_once_with(42, "deepseek-chat", 100)
        mock_clone.assert_called_once()
        mock_dispatch.assert_called_once()
        adapter.register_task.assert_called_once_with(42, result["task_id"])

    async def test_create_task_stored_in_registry(self):
        orch = TaskOrchestrator()
        with patch.object(orch, "_create_group", new_callable=AsyncMock, return_value=1), \
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
