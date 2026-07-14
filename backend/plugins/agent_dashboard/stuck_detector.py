"""
plugins/agent_dashboard/stuck_detector.py — Stuck Detection

Background loop that monitors active tasks for signs of being stuck:
  - No events received for TIMEOUT_SEC seconds
  - Tool execution running unusually long
  - Same tool called repeatedly (doom loop at the dashboard level)

When a task is detected as stuck, its status is updated and a notification
is pushed to dashboard WebSocket clients so the frontend can show a
"Retry" button.
"""
import asyncio
import logging
import time

log = logging.getLogger(__name__)

# Seconds without any event before a task is considered stuck
STUCK_TIMEOUT_SEC = 180  # 3 minutes

# Check interval for the background loop
CHECK_INTERVAL_SEC = 15


class StuckDetector:
    """Monitors active tasks and detects stuck/hung states."""

    def __init__(self, adapter):
        """
        Args:
            adapter: ProgressAdapter instance to read state from and push updates
        """
        self._adapter = adapter
        self._running = False

    async def run(self):
        """Main loop: periodically check all active tasks for stuck state."""
        self._running = True
        log.info("StuckDetector: started (timeout=%ds, interval=%ds)",
                 STUCK_TIMEOUT_SEC, CHECK_INTERVAL_SEC)

        while self._running:
            try:
                await asyncio.sleep(CHECK_INTERVAL_SEC)
                self._check_all()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("StuckDetector: error in check loop")

    def stop(self):
        """Signal the loop to stop."""
        self._running = False

    def _check_all(self):
        """Check all active tasks for stuck state."""
        now = time.time()
        for group_id in list(self._adapter._active_groups):
            state = self._adapter._states.get(group_id)
            if not state:
                continue

            # Skip tasks that are already in a terminal state
            if state.status in ("done", "error", "aborted", "stuck"):
                continue

            # Check timeout: no events for STUCK_TIMEOUT_SEC
            idle_sec = now - state.last_event_at
            if idle_sec > STUCK_TIMEOUT_SEC:
                log.warning(
                    "StuckDetector: group %d stuck (no events for %.0fs, phase=%s)",
                    group_id, idle_sec, state.phase,
                )
                state.status = "stuck"
                state.detail = f"卡死检测: {idle_sec:.0f}s 无新事件（阶段: {state.phase}）"
                self._adapter._push_update(group_id, state)

    def force_check(self, group_id: int) -> bool:
        """Manually check a specific group. Returns True if stuck."""
        state = self._adapter._states.get(group_id)
        if not state or state.status in ("done", "error", "aborted"):
            return False

        idle_sec = time.time() - state.last_event_at
        if idle_sec > STUCK_TIMEOUT_SEC:
            state.status = "stuck"
            state.detail = f"卡死检测: {idle_sec:.0f}s 无新事件"
            self._adapter._push_update(group_id, state)
            return True
        return False
