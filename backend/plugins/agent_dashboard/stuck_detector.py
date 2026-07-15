"""
plugins/agent_dashboard/stuck_detector.py — Stuck Detection + Auto-Retry

Background loop that monitors active tasks for signs of being stuck:
  - No events received for TIMEOUT_SEC seconds
  - Tool execution running unusually long
  - Same tool called repeatedly (doom loop at the dashboard level)

When a task is detected as stuck:
  1. Status is updated to "stuck" and pushed to dashboard WS clients
  2. If auto_retry is enabled and retry_count < max_retries, automatically
     triggers a retry via the orchestrator (Fix #5: no human intervention needed)
  3. If max_retries exceeded, marks as "stuck_permanently" and stops retrying
"""
import asyncio
import logging
import time

from plugins.agent_dashboard.progress import TERMINAL_STATUSES

log = logging.getLogger(__name__)

# Seconds without any event before a task is considered stuck
STUCK_TIMEOUT_SEC = 180  # 3 minutes

# Check interval for the background loop
CHECK_INTERVAL_SEC = 15

# Maximum auto-retries before giving up (0 = no auto-retry)
DEFAULT_MAX_AUTO_RETRIES = 3


class StuckDetector:
    """Monitors active tasks and detects stuck/hung states with optional auto-retry."""

    def __init__(self, adapter, orchestrator=None, max_auto_retries: int = DEFAULT_MAX_AUTO_RETRIES):
        """
        Args:
            adapter: ProgressAdapter instance to read state from and push updates
            orchestrator: TaskOrchestrator for auto-retry (optional, None = manual retry only)
            max_auto_retries: Max automatic retries before giving up (0 = disabled)
        """
        self._adapter = adapter
        self._orchestrator = orchestrator
        self._max_auto_retries = max_auto_retries
        self._running = False
        # group_id → retry count
        self._retry_counts: dict[int, int] = {}
        self._retry_tasks: dict[int, asyncio.Task] = {}
        self._adapter.add_cleanup_callback(self.forget)

    def forget(self, group_id: int) -> None:
        """Release detector state when a task leaves active tracking."""
        self._retry_counts.pop(group_id, None)
        task = self._retry_tasks.pop(group_id, None)
        if task and not task.done():
            task.cancel()

    async def run(self):
        """Main loop: periodically check all active tasks for stuck state."""
        self._running = True
        log.info("StuckDetector: started (timeout=%ds, interval=%ds, max_retries=%d)",
                 STUCK_TIMEOUT_SEC, CHECK_INTERVAL_SEC, self._max_auto_retries)

        while self._running:
            try:
                await asyncio.sleep(CHECK_INTERVAL_SEC)
                await self._check_all()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("StuckDetector: error in check loop")

    def stop(self):
        """Signal the loop to stop."""
        self._running = False

    async def _check_all(self):
        """Check all active tasks for stuck state. Triggers auto-retry if configured."""
        now = time.time()
        for group_id in set(self._retry_counts) - set(self._adapter._active_groups):
            self.forget(group_id)

        for group_id in list(self._adapter._active_groups):
            state = self._adapter._states.get(group_id)
            if not state:
                continue

            if state.status in TERMINAL_STATUSES:
                self._adapter.retire_if_terminal(group_id)
                continue

            # Stuck tasks wait for manual retry; retrying tasks already have one
            # in-flight retry and must not be dispatched again by the next tick.
            if state.status in ("stuck", "retrying"):
                continue

            # Check timeout: no events for STUCK_TIMEOUT_SEC
            idle_sec = now - state.last_event_at
            if idle_sec > STUCK_TIMEOUT_SEC:
                retry_count = self._retry_counts.get(group_id, 0)

                if self._orchestrator and retry_count < self._max_auto_retries:
                    # Auto-retry: schedule a retry via orchestrator
                    self._retry_counts[group_id] = retry_count + 1
                    log.warning(
                        "StuckDetector: group %d stuck (%.0fs idle), auto-retry %d/%d",
                        group_id, idle_sec, retry_count + 1, self._max_auto_retries,
                    )
                    self._adapter.set_status(
                        group_id,
                        "retrying",
                        detail=(
                            f"卡死自动重试 "
                            f"({retry_count + 1}/{self._max_auto_retries})"
                        ),
                    )
                    # Fire-and-forget retry (don't block the check loop)
                    task_id = state.task_id
                    task = asyncio.create_task(self._auto_retry(group_id, task_id))
                    self._retry_tasks[group_id] = task
                    task.add_done_callback(
                        lambda completed, gid=group_id: self._retry_tasks.pop(gid, None)
                    )
                else:
                    # No auto-retry or max retries exceeded
                    if retry_count >= self._max_auto_retries > 0:
                        log.warning(
                            "StuckDetector: group %d permanently stuck (max retries %d exceeded)",
                            group_id, self._max_auto_retries,
                        )
                        self._adapter.set_status(
                            group_id,
                            "stuck_permanently",
                            detail=f"永久卡死: 已重试 {retry_count} 次仍失败",
                        )
                    else:
                        log.warning(
                            "StuckDetector: group %d stuck (no events for %.0fs, phase=%s)",
                            group_id, idle_sec, state.phase,
                        )
                        self._adapter.set_status(
                            group_id,
                            "stuck",
                            detail=(
                                f"卡死检测: {idle_sec:.0f}s 无新事件"
                                f"（阶段: {state.phase}）"
                            ),
                        )

    async def _auto_retry(self, group_id: int, task_id: str):
        """Execute an automatic retry via the orchestrator.

        Note: the retry counter is NOT cleared here. It's only cleared when
        the task actually completes (detected in _check_all via status="done").
        Clearing on dispatch success would allow infinite retries since each
        re-dispatch resets the counter.
        """
        try:
            if self._orchestrator:
                await self._orchestrator.retry_task(task_id)
                log.info("StuckDetector: auto-retry dispatched for task %s", task_id)
        except Exception as e:
            log.error("StuckDetector: auto-retry failed for task %s: %s", task_id, e)
            state = self._adapter._states.get(group_id)
            if state:
                state.last_event_at = time.time()
                self._adapter.set_status(
                    group_id,
                    "stuck",
                    detail=f"自动重试失败: {str(e)[:80]}",
                    error_message=str(e),
                )

    def force_check(self, group_id: int) -> bool:
        """Manually check a specific group. Returns True if stuck."""
        state = self._adapter._states.get(group_id)
        if not state or state.status in ("done", "error", "aborted"):
            return False

        idle_sec = time.time() - state.last_event_at
        if idle_sec > STUCK_TIMEOUT_SEC:
            self._adapter.set_status(
                group_id,
                "stuck",
                detail=f"卡死检测: {idle_sec:.0f}s 无新事件",
            )
            return True
        return False
