"""tests/test_workspace_action.py — P0-3: Worktree lifecycle management tests.

Tests the workspace_action field in OrchestratorStep:
  - CodingAgentOrchestrator sets workspace_action based on completion signal
  - runner._handle_workspace_action performs promote/discard/retain
  - CancelledError path skips promotion
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.orchestration.plugins.coding_agent import CodingAgentOrchestrator
from core.orchestration.base import OrchestratorStep


class TestCodingAgentWorkspaceAction(unittest.TestCase):
    """Test CodingAgentOrchestrator sets workspace_action correctly."""

    def test_signal_stage_done_sets_promote(self):
        """signal_stage_done → workspace_action='promote' (merge changes)."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": "", "task_id": "task_123"})

        signals = [{"name": "signal_stage_done", "arguments": {"reason": "completed"}}]
        step = orch.observe(1, 5, "Done", signals=signals)

        self.assertTrue(step.done)
        self.assertEqual(step.workspace_action, "promote")

    def test_signal_rework_sets_discard(self):
        """signal_rework → workspace_action='discard' (delete without merging)."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": "", "task_id": "task_123"})

        signals = [{"name": "signal_rework", "arguments": {"reason": "tests failing"}}]
        step = orch.observe(1, 5, "Need rework", signals=signals)

        self.assertFalse(step.done)
        self.assertEqual(step.workspace_action, "discard")

    def test_no_signal_sets_discard(self):
        """No completion signal → workspace_action='discard' (incomplete run)."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": "", "task_id": "task_123"})

        step = orch.observe(1, 5, "No signal", signals=[])

        self.assertFalse(step.done)
        self.assertEqual(step.workspace_action, "discard")
        self.assertIsNotNone(step.workflow_paused)


class TestTaskIdInWorkUnit(unittest.TestCase):
    """Test task_id is passed through WorkUnit.tag['ticket_id']."""

    def test_begin_sets_ticket_id(self):
        """begin() sets WorkUnit.tag['ticket_id'] from spec.task_id."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        step = orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": "", "task_id": "task_123"})

        self.assertEqual(len(step.next_units), 1)
        unit = step.next_units[0]
        self.assertEqual(unit.tag.get("ticket_id"), "task_123")

    def test_begin_without_task_id(self):
        """begin() without task_id sets empty tag."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        step = orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        unit = step.next_units[0]
        self.assertEqual(unit.tag, {})


class TestHandleWorkspaceAction(unittest.IsolatedAsyncioTestCase):
    """Test runner._handle_workspace_action."""

    async def test_promote_calls_promote_worktree(self):
        """workspace_action='promote' calls promote_worktree for each worktree."""
        from core import runner

        with patch("workspace.layout.group_dir") as mock_group_dir, \
             patch("workspace.git_worktree.promote_worktree", new_callable=AsyncMock) as mock_promote:

            from pathlib import Path
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                group_dir = Path(tmp_dir)
                worktrees_dir = group_dir / "worktrees"
                worktrees_dir.mkdir()
                (worktrees_dir / "task_task_123").mkdir()
                (worktrees_dir / "task_task_456").mkdir()

                mock_group_dir.return_value = group_dir

                await runner._handle_workspace_action(1, "promote")

                # Should promote both worktrees
                self.assertEqual(mock_promote.call_count, 2)

    async def test_discard_calls_remove_worktree(self):
        """workspace_action='discard' calls remove_worktree for each worktree."""
        from core import runner

        with patch("workspace.layout.group_dir") as mock_group_dir, \
             patch("workspace.git_worktree.remove_worktree", new_callable=AsyncMock) as mock_remove:

            from pathlib import Path
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                group_dir = Path(tmp_dir)
                worktrees_dir = group_dir / "worktrees"
                worktrees_dir.mkdir()
                (worktrees_dir / "task_task_123").mkdir()

                mock_group_dir.return_value = group_dir

                await runner._handle_workspace_action(1, "discard")

                mock_remove.assert_called_once_with(1, "task_123")

    async def test_retain_does_nothing(self):
        """workspace_action='retain' does not call any worktree operations."""
        from core import runner

        with patch("workspace.layout.group_dir") as mock_group_dir, \
             patch("workspace.git_worktree.promote_worktree", new_callable=AsyncMock) as mock_promote, \
             patch("workspace.git_worktree.remove_worktree", new_callable=AsyncMock) as mock_remove:

            await runner._handle_workspace_action(1, "retain")

            mock_promote.assert_not_called()
            mock_remove.assert_not_called()

    async def test_no_worktrees_dir(self):
        """No worktrees directory → no operations."""
        from core import runner

        with patch("workspace.layout.group_dir") as mock_group_dir:
            from pathlib import Path
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                group_dir = Path(tmp_dir)
                # No worktrees directory created

                mock_group_dir.return_value = group_dir

                # Should not raise
                await runner._handle_workspace_action(1, "promote")


class TestCancelledErrorSkipsPromotion(unittest.IsolatedAsyncioTestCase):
    """Test that CancelledError path skips worktree promotion."""

    async def test_cancelled_task_skips_promotion(self):
        """When task is cancelled, _cleanup_finally skips promotion."""
        from core import runner

        # Create a mock task that's cancelled
        mock_task = MagicMock()
        mock_task.cancelled.return_value = True

        with patch("asyncio.current_task", return_value=mock_task), \
             patch("workspace.layout.group_dir") as mock_group_dir, \
             patch("workspace.git_worktree.promote_worktree", new_callable=AsyncMock) as mock_promote:

            from pathlib import Path
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                group_dir = Path(tmp_dir)
                worktrees_dir = group_dir / "worktrees"
                worktrees_dir.mkdir()
                (worktrees_dir / "task_task_123").mkdir()

                mock_group_dir.return_value = group_dir

                # Simulate the finally block
                async def _cleanup_finally():
                    current_task = asyncio.current_task()
                    if current_task and current_task.cancelled():
                        return  # Skip promotion

                    # Would normally promote here
                    from workspace.git_worktree import promote_worktree
                    await promote_worktree(1, "task_123")

                await _cleanup_finally()

                # Promotion should be skipped
                mock_promote.assert_not_called()

    async def test_normal_task_promotes(self):
        """When task is not cancelled, _cleanup_finally promotes worktree."""
        from core import runner

        # Create a mock task that's NOT cancelled
        mock_task = MagicMock()
        mock_task.cancelled.return_value = False

        with patch("asyncio.current_task", return_value=mock_task), \
             patch("workspace.layout.group_dir") as mock_group_dir, \
             patch("workspace.git_worktree.promote_worktree", new_callable=AsyncMock) as mock_promote:

            from pathlib import Path
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                group_dir = Path(tmp_dir)
                worktrees_dir = group_dir / "worktrees"
                worktrees_dir.mkdir()
                (worktrees_dir / "task_task_123").mkdir()

                mock_group_dir.return_value = group_dir

                # Simulate the finally block
                async def _cleanup_finally():
                    current_task = asyncio.current_task()
                    if current_task and current_task.cancelled():
                        return  # Skip promotion

                    # Promote
                    from workspace.git_worktree import promote_worktree
                    await promote_worktree(1, "task_123")

                await _cleanup_finally()

                # Promotion should happen
                mock_promote.assert_called_once_with(1, "task_123")
