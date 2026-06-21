"""Per-group run serialization (concurrency bug fix).

Bug: rapid successive messages to a group each fire-and-forget a `run_unit`
task with no serialization, so the same bot's runs overlap, interleave their
LLM output, and replies get lost. Fix: run_unit acquires a per-group lock so
only one run executes at a time per group (and reloads fresh history when it
acquires). Different groups must still run concurrently.
"""
import asyncio
import os
import sys
import unittest
from contextlib import asynccontextmanager, ExitStack
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import bg, runner
from core.orchestration.base import WorkUnit


@asynccontextmanager
async def _fake_db_cm():
    yield object()


class _ConcurrencyProbe:
    """Fake executor that records the peak number of overlapping run()s."""
    def __init__(self):
        self.now = 0
        self.peak = 0

    async def run(self, ctx):
        self.now += 1
        self.peak = max(self.peak, self.now)
        await asyncio.sleep(0.05)   # hold the "run" open so overlap is observable
        self.now -= 1
        return SimpleNamespace(full_text="", signals=None)  # empty → no observe()


def _fake_orch():
    # start_time(gid) -> None makes run_unit skip the time-filter / compaction block.
    return SimpleNamespace(start_time=lambda gid: None)


def _unit():
    return WorkUnit(bot={"id": 1, "name": "Dev"}, executor_id="fake", trigger_msg="hi")


class TestRunUnitSerialization(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bg._bg_tasks.clear()
        bg._group_tasks.clear()
        bg._group_run_locks.clear()

    def _patches(self, probe):
        stack = ExitStack()
        for p in (
            patch("core.runner.global_db", new=lambda: _fake_db_cm()),
            patch("core.runner.get_db", new=lambda: _fake_db_cm()),
            patch("core.runner.get_members", new=AsyncMock(return_value=[])),
            patch("core.runner.get_messages", new=AsyncMock(return_value=[])),
            patch("core.runner.exec_registry.get", new=MagicMock(return_value=probe)),
        ):
            stack.enter_context(p)
        return stack

    async def test_same_group_runs_do_not_overlap(self):
        probe = _ConcurrencyProbe()
        orch = _fake_orch()
        with self._patches(probe):
            await asyncio.gather(
                runner.run_unit(11, _unit(), orch),
                runner.run_unit(11, _unit(), orch),
                runner.run_unit(11, _unit(), orch),
            )
        self.assertEqual(probe.peak, 1, "same-group runs must serialize (no overlap)")

    async def test_different_groups_run_concurrently(self):
        probe = _ConcurrencyProbe()
        orch = _fake_orch()
        with self._patches(probe):
            await asyncio.gather(
                runner.run_unit(11, _unit(), orch),
                runner.run_unit(22, _unit(), orch),
            )
        self.assertEqual(probe.peak, 2, "different groups must still run concurrently")

    async def test_aborted_run_releases_lock_for_next_run(self):
        # A+B interaction: aborting the in-progress run (coalesce-latest) must
        # release the per-group lock so the replacement run can acquire it and
        # finish — otherwise the bot would deadlock and never answer.
        class _CompletionProbe:
            def __init__(self):
                self.completed = 0

            async def run(self, ctx):
                await asyncio.sleep(0.1)
                self.completed += 1
                return SimpleNamespace(full_text="", signals=None)

        probe = _CompletionProbe()
        orch = _fake_orch()
        with self._patches(probe):
            t1 = bg.spawn_group(11, runner.run_unit(11, _unit(), orch))
            await asyncio.sleep(0.1)        # let t1 acquire the group lock
            bg.abort_group(11)              # coalesce-latest cancels the in-progress run
            t2 = bg.spawn_group(11, runner.run_unit(11, _unit(), orch))
            await asyncio.wait_for(asyncio.gather(t1, t2, return_exceptions=True), timeout=2)

        self.assertTrue(t1.cancelled(), "aborted run must be cancelled")
        self.assertEqual(probe.completed, 1, "replacement run must acquire the lock and finish")


if __name__ == "__main__":
    unittest.main()
