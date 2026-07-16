"""tests/test_stuck_detector.py — StuckDetector unit tests.

Tests the stuck detection logic:
  - Tasks with no recent events are flagged as stuck
  - Terminal-state tasks are skipped
  - Manual force_check works correctly
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock
from plugins.agent_dashboard.stuck_detector import StuckDetector, STUCK_TIMEOUT_SEC
from plugins.agent_dashboard.progress import ProgressAdapter, TaskProgress


class TestStuckDetection(unittest.IsolatedAsyncioTestCase):

    def _make_adapter_with_task(self, group_id=1, status="running", idle_sec=0):
        """Create adapter with a task in a given state."""
        adapter = ProgressAdapter()
        state = adapter.register_task(group_id, f"task_{group_id}")
        state.status = status
        state.last_event_at = time.time() - idle_sec
        return adapter

    async def test_stuck_after_timeout(self):
        """Task with no events for > STUCK_TIMEOUT_SEC is marked stuck."""
        adapter = self._make_adapter_with_task(idle_sec=STUCK_TIMEOUT_SEC + 10)
        detector = StuckDetector(adapter)
        await detector._check_all()

        state = adapter._states[1]
        self.assertEqual(state.status, "stuck")
        self.assertIn("卡死检测", state.detail)

    async def test_not_stuck_within_timeout(self):
        """Task with recent events is NOT marked stuck."""
        adapter = self._make_adapter_with_task(idle_sec=10)
        detector = StuckDetector(adapter)
        await detector._check_all()

        state = adapter._states[1]
        self.assertEqual(state.status, "running")

    async def test_done_tasks_skipped(self):
        """Tasks in 'done' status are not checked."""
        adapter = self._make_adapter_with_task(status="done", idle_sec=STUCK_TIMEOUT_SEC + 100)
        detector = StuckDetector(adapter)
        await detector._check_all()

        state = adapter._states[1]
        self.assertEqual(state.status, "done")  # unchanged

    async def test_error_tasks_skipped(self):
        """Tasks in 'error' status are not checked."""
        adapter = self._make_adapter_with_task(status="error", idle_sec=STUCK_TIMEOUT_SEC + 100)
        detector = StuckDetector(adapter)
        await detector._check_all()

        state = adapter._states[1]
        self.assertEqual(state.status, "error")  # unchanged

    async def test_aborted_tasks_skipped(self):
        adapter = self._make_adapter_with_task(status="aborted", idle_sec=STUCK_TIMEOUT_SEC + 100)
        detector = StuckDetector(adapter)
        await detector._check_all()

        self.assertEqual(adapter._states[1].status, "aborted")

    async def test_already_stuck_tasks_skipped(self):
        """Already stuck tasks are not re-flagged."""
        adapter = self._make_adapter_with_task(status="stuck", idle_sec=STUCK_TIMEOUT_SEC + 100)
        detector = StuckDetector(adapter)
        await detector._check_all()

        self.assertEqual(adapter._states[1].status, "stuck")  # unchanged

    async def test_multiple_tasks_independent(self):
        """Multiple tasks are checked independently."""
        adapter = ProgressAdapter()
        # Task 1: recent (not stuck)
        s1 = adapter.register_task(1, "t1")
        s1.last_event_at = time.time() - 10
        # Task 2: old (stuck)
        s2 = adapter.register_task(2, "t2")
        s2.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10

        detector = StuckDetector(adapter)
        await detector._check_all()

        self.assertEqual(adapter._states[1].status, "running")
        self.assertEqual(adapter._states[2].status, "stuck")


class TestForceCheck(unittest.TestCase):

    def test_force_check_detects_stuck(self):
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "t1")
        state.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 5

        detector = StuckDetector(adapter)
        result = detector.force_check(1)

        self.assertTrue(result)
        self.assertEqual(state.status, "stuck")

    def test_force_check_not_stuck(self):
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "t1")
        state.last_event_at = time.time() - 5  # very recent

        detector = StuckDetector(adapter)
        result = detector.force_check(1)

        self.assertFalse(result)
        self.assertEqual(state.status, "running")

    def test_force_check_nonexistent_group(self):
        adapter = ProgressAdapter()
        detector = StuckDetector(adapter)
        result = detector.force_check(999)
        self.assertFalse(result)

    def test_force_check_done_task(self):
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "t1")
        state.status = "done"

        detector = StuckDetector(adapter)
        result = detector.force_check(1)
        self.assertFalse(result)


class TestStuckDetectorLoop(unittest.IsolatedAsyncioTestCase):

    async def test_stop_cancels_loop(self):
        adapter = ProgressAdapter()
        detector = StuckDetector(adapter)

        import asyncio
        task = asyncio.create_task(detector.run())
        await asyncio.sleep(0.05)  # let it start
        detector.stop()
        await asyncio.sleep(0.05)  # let it stop

        self.assertTrue(task.done() or not detector._running)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestAutoRetry(unittest.IsolatedAsyncioTestCase):

    async def test_auto_retry_triggers_on_stuck(self):
        """When orchestrator is provided, stuck task triggers auto-retry."""
        from unittest.mock import MagicMock, AsyncMock
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "task_1")
        state.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10

        mock_orch = MagicMock()
        mock_orch.retry_task = AsyncMock(return_value={"status": "restarted"})

        detector = StuckDetector(adapter, orchestrator=mock_orch, max_auto_retries=3)
        await detector._check_all()

        self.assertEqual(state.status, "running")
        self.assertEqual(detector._retry_counts[1], 1)
        # Let the background task complete
        import asyncio
        await asyncio.sleep(0.01)

    async def test_auto_retry_increments_count(self):
        """Each stuck detection increments the retry counter."""
        from unittest.mock import MagicMock, AsyncMock
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "task_1")
        state.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10

        mock_orch = MagicMock()
        mock_orch.retry_task = AsyncMock(return_value={"status": "restarted"})
        detector = StuckDetector(adapter, orchestrator=mock_orch, max_auto_retries=3)
        # Simulate 3 stuck checks
        await detector._check_all()
        await asyncio.sleep(0)
        state.status = "running"  # reset (simulates retry succeeded briefly)
        state.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10  # stuck again
        await detector._check_all()
        await asyncio.sleep(0)
        state.status = "running"
        state.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10  # stuck again
        await detector._check_all()
        await asyncio.sleep(0)

        self.assertEqual(detector._retry_counts[1], 3)

    async def test_auto_retry_gives_up_at_max(self):
        """When max retries exceeded, marks as stuck_permanently."""
        from unittest.mock import MagicMock, AsyncMock
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "task_1")
        state.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10

        mock_orch = MagicMock()
        mock_orch.retry_task = AsyncMock(return_value={"status": "restarted"})
        async def terminate(_task_id, reason):
            adapter.set_status(
                1, "stuck_permanently", detail=reason, project=False
            )
        mock_orch.terminate_permanently_stuck = AsyncMock(side_effect=terminate)

        detector = StuckDetector(adapter, orchestrator=mock_orch, max_auto_retries=2)
        detector._retry_counts[1] = 2  # already at max

        await detector._check_all()
        await asyncio.sleep(0)
        self.assertEqual(state.status, "stuck_permanently")
        self.assertIn("永久卡死", state.detail)
        self.assertNotIn(1, adapter._active_groups)
        self.assertNotIn(1, detector._retry_counts)
        mock_orch.terminate_permanently_stuck.assert_awaited_once()

    async def test_no_auto_retry_without_orchestrator(self):
        """Without orchestrator, falls back to manual retry (status=stuck)."""
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "task_1")
        state.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10

        detector = StuckDetector(adapter)  # no orchestrator
        await detector._check_all()

        self.assertEqual(state.status, "stuck")

    async def test_no_auto_retry_when_disabled(self):
        """With max_auto_retries=0, auto-retry is disabled."""
        from unittest.mock import MagicMock
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "task_1")
        state.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10

        mock_orch = MagicMock()
        detector = StuckDetector(adapter, orchestrator=mock_orch, max_auto_retries=0)
        await detector._check_all()

        self.assertEqual(state.status, "stuck")
        mock_orch.retry_task.assert_not_called()


class TestAutoRetryAsync(unittest.IsolatedAsyncioTestCase):

    async def test_auto_retry_calls_orchestrator(self):
        """_auto_retry calls orchestrator.retry_task. Counter NOT cleared on dispatch."""
        from unittest.mock import MagicMock, AsyncMock
        adapter = ProgressAdapter()
        adapter.register_task(1, "task_1")

        mock_orch = MagicMock()
        mock_orch.retry_task = AsyncMock(return_value={"status": "restarted"})

        detector = StuckDetector(adapter, orchestrator=mock_orch)
        detector._retry_counts[1] = 2

        await detector._auto_retry(1, "task_1")

        mock_orch.retry_task.assert_called_once_with("task_1", automatic=True)
        # Counter NOT cleared on dispatch — only cleared on task completion
        self.assertEqual(detector._retry_counts.get(1), 2)

    async def test_auto_retry_handles_failure(self):
        """_auto_retry logs error but doesn't crash when retry fails."""
        from unittest.mock import MagicMock, AsyncMock
        adapter = ProgressAdapter()
        adapter.register_task(1, "task_1")

        mock_orch = MagicMock()
        mock_orch.retry_task = AsyncMock(side_effect=RuntimeError("retry failed"))

        detector = StuckDetector(adapter, orchestrator=mock_orch)
        # Should not raise
        await detector._auto_retry(1, "task_1")
        self.assertEqual(adapter._states[1].status, "stuck")


class TestRetryCounterClearedOnCompletion(unittest.IsolatedAsyncioTestCase):

    async def test_counter_cleared_immediately_on_terminal_event(self):
        for event in (
            {"type": "workflow_update", "done": True},
            {"type": "stream_error", "message": "provider failed"},
            {"type": "stream_aborted"},
        ):
            adapter = ProgressAdapter()
            adapter.register_task(1, "task_1")
            detector = StuckDetector(adapter)
            detector._retry_counts[1] = 2

            adapter.on_event(1, event)

            self.assertNotIn(1, detector._retry_counts)
            self.assertNotIn(1, adapter._active_groups)

    async def test_counter_cleared_when_task_is_removed(self):
        adapter = ProgressAdapter()
        adapter.register_task(1, "task_1")
        detector = StuckDetector(adapter)
        detector._retry_counts[1] = 2

        adapter.remove_task(1)

        self.assertNotIn(1, detector._retry_counts)

    async def test_counter_cleared_when_task_done(self):
        """Retry counter is cleared when task reaches 'done' status, not on dispatch."""
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "task_1")
        state.status = "done"  # task completed
        state.last_event_at = time.time()

        detector = StuckDetector(adapter)
        detector._retry_counts[1] = 3  # had 3 retries

        await detector._check_all()
        self.assertNotIn(1, detector._retry_counts)  # cleared on completion

    async def test_counter_not_cleared_on_running(self):
        """Retry counter persists while task is still running."""
        adapter = ProgressAdapter()
        state = adapter.register_task(1, "task_1")
        state.status = "running"
        state.last_event_at = time.time() - 5  # recent

        detector = StuckDetector(adapter)
        detector._retry_counts[1] = 2

        await detector._check_all()
        self.assertEqual(detector._retry_counts.get(1), 2)  # not cleared
