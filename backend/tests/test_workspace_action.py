"""Task-scoped worktree lifecycle tests."""

import asyncio
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.orchestration.base import WorkUnit
from core.orchestration.plugins.coding_agent import CodingAgentOrchestrator


class TestCodingAgentWorkspaceAction(unittest.TestCase):
    def setUp(self):
        self.orch = CodingAgentOrchestrator()
        self.bot = {"id": 5, "name": "Agent"}

    def _begin(self):
        return self.orch.begin(
            1,
            {
                "bots": [self.bot],
                "requirements": "test",
                "task_id": "agent_123",
            },
        )

    def test_signal_stage_done_promotes(self):
        self._begin()
        step = self.orch.observe(
            1, 5, "Done", signals=[{"name": "signal_stage_done", "arguments": {}}]
        )
        self.assertTrue(step.done)
        self.assertEqual(step.workspace_action, "promote")

    def test_signal_rework_discards(self):
        self._begin()
        step = self.orch.observe(
            1, 5, "Rework", signals=[{"name": "signal_rework", "arguments": {}}]
        )
        self.assertFalse(step.done)
        self.assertEqual(step.workspace_action, "discard")
        self.assertEqual(step.workflow_paused.reason, "rework_requested")

    def test_missing_signal_preserves_workspace_and_pauses(self):
        self._begin()
        step = self.orch.observe(1, 5, "No signal", signals=[])
        self.assertFalse(step.done)
        self.assertIsNone(step.workspace_action)
        self.assertEqual(step.workflow_paused.reason, "completion_signal_missing")


class TestTaskIdPropagation(unittest.TestCase):
    def setUp(self):
        self.orch = CodingAgentOrchestrator()
        self.bot = {"id": 5, "name": "Agent"}

    def test_begin_sets_ticket_id(self):
        step = self.orch.begin(
            1, {"bots": [self.bot], "requirements": "test", "task_id": "agent_123"}
        )
        self.assertEqual(step.next_units[0].tag, {"ticket_id": "agent_123"})

    def test_parse_spec_preserves_task_id(self):
        spec = self.orch.parse_spec(
            {"bot_id": 5, "requirements": "test", "task_id": "agent_123"},
            {5: self.bot},
        )
        self.assertEqual(spec["task_id"], "agent_123")

    def test_resume_preserves_task_id(self):
        self.orch.begin(
            1, {"bots": [self.bot], "requirements": "test", "task_id": "agent_123"}
        )
        self.assertEqual(
            self.orch.resume_units(1)[0].tag, {"ticket_id": "agent_123"}
        )


class TestHandleWorkspaceAction(unittest.IsolatedAsyncioTestCase):
    async def test_promote_targets_exact_task(self):
        from core import runner

        with patch(
            "workspace.git_worktree.promote_worktree", new_callable=AsyncMock
        ) as promote:
            await runner._handle_workspace_action(1, "agent_123", "promote")
        promote.assert_awaited_once_with(1, "agent_123")

    async def test_discard_targets_exact_task(self):
        from core import runner

        with patch(
            "workspace.git_worktree.remove_worktree", new_callable=AsyncMock
        ) as remove:
            await runner._handle_workspace_action(1, "agent_123", "discard")
        remove.assert_awaited_once_with(1, "agent_123")

    async def test_mutating_action_requires_task_id(self):
        from core import runner

        with self.assertRaisesRegex(ValueError, "requires a task_id"):
            await runner._handle_workspace_action(1, "", "promote")


class TestRunnerWorktreeLifecycle(unittest.IsolatedAsyncioTestCase):
    def _db_cm(self):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=MagicMock())
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    async def test_real_cancellation_discards_only_current_task(self):
        from core import runner

        started = asyncio.Event()

        class BlockingExecutor:
            async def run(self, _ctx):
                started.set()
                await asyncio.Event().wait()

        unit = WorkUnit(
            bot={"id": 5, "name": "Agent"},
            executor_id="blocking",
            trigger_msg="go",
            tag={"ticket_id": "agent_123"},
        )
        cm = self._db_cm()

        class StubOrchestrator:
            def start_time(self, _group_id):
                return None

        with patch.object(runner.asyncio, "sleep", new=AsyncMock()), \
             patch.object(runner, "get_members", new=AsyncMock(return_value=[])), \
             patch.object(runner, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(runner, "global_db", return_value=cm), \
             patch.object(runner, "get_db", return_value=cm), \
             patch.object(runner.exec_registry, "get", return_value=BlockingExecutor()), \
             patch("workspace.git_worktree.create_worktree", new=AsyncMock(return_value=Path("/tmp/task_agent_123"))), \
             patch("workspace.git_worktree.use_worktree", return_value=nullcontext()), \
             patch("workspace.git_worktree.remove_worktree", new_callable=AsyncMock) as remove, \
             patch("workspace.git_worktree.promote_worktree", new_callable=AsyncMock) as promote:
            task = asyncio.create_task(
                runner._run_unit_body(1, unit, StubOrchestrator())
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        remove.assert_awaited_once_with(1, "agent_123")
        promote.assert_not_called()

    async def test_workspace_is_promoted_before_done_is_published(self):
        from bus.events import WorkflowUpdate
        from core import runner
        from core.orchestration.base import OrchestratorStep

        events = []

        async def promote(_group_id, _task_id):
            events.append("promote")

        async def publish(event):
            if isinstance(event, WorkflowUpdate):
                events.append("done" if event.done else "not_done")

        orch = MagicMock()
        orch.serialize.return_value = None
        with patch("workspace.git_worktree.promote_worktree", new=promote), \
             patch.object(runner.bus, "publish", new=publish), \
             patch.object(runner.workflow_store, "clear_state", new=AsyncMock()):
            await runner.apply_step(
                1,
                orch,
                OrchestratorStep(
                    done=True,
                    broadcast_state=True,
                    workspace_action="promote",
                ),
                workspace_task_id="agent_123",
            )

        self.assertEqual(events, ["promote", "done"])

    async def test_signal_only_result_still_promotes_exact_task(self):
        from core import runner
        from executors.base import ExecutionResult

        class SignalOnlyExecutor:
            async def run(self, _ctx):
                return ExecutionResult(
                    full_text="",
                    msg_id=None,
                    signals=[
                        {
                            "name": "signal_stage_done",
                            "arguments": {"reason": "tests passed"},
                        }
                    ],
                )

        bot = {"id": 5, "name": "Agent"}
        orch = CodingAgentOrchestrator()
        step = orch.begin(
            1,
            {"bots": [bot], "requirements": "test", "task_id": "agent_123"},
        )
        unit = step.next_units[0]
        cm = self._db_cm()

        with patch.object(runner.asyncio, "sleep", new=AsyncMock()), \
             patch.object(runner, "get_members", new=AsyncMock(return_value=[bot])), \
             patch.object(runner, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(runner, "global_db", return_value=cm), \
             patch.object(runner, "get_db", return_value=cm), \
             patch.object(runner.exec_registry, "get", return_value=SignalOnlyExecutor()), \
             patch("workspace.git_worktree.create_worktree", new=AsyncMock(return_value=Path("/tmp/task_agent_123"))), \
             patch("workspace.git_worktree.use_worktree", return_value=nullcontext()), \
             patch("workspace.git_worktree.promote_worktree", new_callable=AsyncMock) as promote, \
             patch("workspace.git_worktree.remove_worktree", new_callable=AsyncMock) as remove, \
             patch.object(runner.bus, "publish", new_callable=AsyncMock), \
             patch.object(runner.workflow_store, "clear_state", new_callable=AsyncMock), \
             patch.object(runner.workflow_store, "save_state", new_callable=AsyncMock):
            await runner._run_unit_body(1, unit, orch)

        promote.assert_awaited_once_with(1, "agent_123")
        remove.assert_not_awaited()


class TestRealDispatchTaskId(unittest.IsolatedAsyncioTestCase):
    async def test_start_workflow_preserves_task_id_into_spawned_unit(self):
        from runtime import dispatch

        bot = {"id": 5, "name": "Agent", "type": "bot"}
        applied = []

        async def capture_apply(_group_id, step):
            applied.append(step)

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=MagicMock())
        cm.__aexit__ = AsyncMock(return_value=False)
        with patch.object(dispatch.db, "global_db", return_value=cm), \
             patch.object(dispatch.db, "get_members", new=AsyncMock(return_value=[bot])), \
             patch("core.workflow.apply", new=capture_apply):
            await dispatch.dispatch_start_workflow(
                {
                    "group_id": 1,
                    "body": {
                        "orchestrator_id": "coding_agent_v1",
                        "bot_id": 5,
                        "requirements": "test",
                        "task_id": "agent_123",
                    },
                }
            )

        self.assertEqual(applied[0].next_units[0].tag, {"ticket_id": "agent_123"})


if __name__ == "__main__":
    unittest.main()
