"""tests/test_stuck_detector.py — StuckDetector unit tests.

Tests the stuck detection logic:
  - Tasks with no recent events are flagged as stuck
  - Terminal-state tasks are skipped
  - Manual force_check works correctly
"""
import time
import unittest
from unittest.mock import MagicMock
from plugins.agent_dashboard.stuck_detector import StuckDetector, STUCK_TIMEOUT_SEC
from plugins.agent_dashboard.progress import ProgressAdapter, TaskProgress


class TestStuckDetection(unittest.TestCase):

    def _make_adapter_with_task(self, group_id=1, status="running", idle_sec=0):
        """Create adapter with a task in a given state."""
        adapter = ProgressAdapter()
        state = adapter.register_task(group_id, f"task_{group_id}")
        state.status = status
        state.last_event_at = time.time() - idle_sec
        return adapter

    def test_stuck_after_timeout(self):
        """Task with no events for > STUCK_TIMEOUT_SEC is marked stuck."""
        adapter = self._make_adapter_with_task(idle_sec=STUCK_TIMEOUT_SEC + 10)
        detector = StuckDetector(adapter)
        detector._check_all()

        state = adapter._states[1]
        self.assertEqual(state.status, "stuck")
        self.assertIn("卡死检测", state.detail)

    def test_not_stuck_within_timeout(self):
        """Task with recent events is NOT marked stuck."""
        adapter = self._make_adapter_with_task(idle_sec=10)
        detector = StuckDetector(adapter)
        detector._check_all()

        state = adapter._states[1]
        self.assertEqual(state.status, "running")

    def test_done_tasks_skipped(self):
        """Tasks in 'done' status are not checked."""
        adapter = self._make_adapter_with_task(status="done", idle_sec=STUCK_TIMEOUT_SEC + 100)
        detector = StuckDetector(adapter)
        detector._check_all()

        state = adapter._states[1]
        self.assertEqual(state.status, "done")  # unchanged

    def test_error_tasks_skipped(self):
        """Tasks in 'error' status are not checked."""
        adapter = self._make_adapter_with_task(status="error", idle_sec=STUCK_TIMEOUT_SEC + 100)
        detector = StuckDetector(adapter)
        detector._check_all()

        state = adapter._states[1]
        self.assertEqual(state.status, "error")  # unchanged

    def test_aborted_tasks_skipped(self):
        adapter = self._make_adapter_with_task(status="aborted", idle_sec=STUCK_TIMEOUT_SEC + 100)
        detector = StuckDetector(adapter)
        detector._check_all()

        self.assertEqual(adapter._states[1].status, "aborted")

    def test_already_stuck_tasks_skipped(self):
        """Already stuck tasks are not re-flagged."""
        adapter = self._make_adapter_with_task(status="stuck", idle_sec=STUCK_TIMEOUT_SEC + 100)
        detector = StuckDetector(adapter)
        detector._check_all()

        self.assertEqual(adapter._states[1].status, "stuck")  # unchanged

    def test_multiple_tasks_independent(self):
        """Multiple tasks are checked independently."""
        adapter = ProgressAdapter()
        # Task 1: recent (not stuck)
        s1 = adapter.register_task(1, "t1")
        s1.last_event_at = time.time() - 10
        # Task 2: old (stuck)
        s2 = adapter.register_task(2, "t2")
        s2.last_event_at = time.time() - STUCK_TIMEOUT_SEC - 10

        detector = StuckDetector(adapter)
        detector._check_all()

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
