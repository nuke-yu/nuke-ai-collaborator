"""Tests for core.bg — background task registry (DFT-025 / DFT-027).

DFT-025: spawned tasks are held (not GC'd) and their exceptions are logged.
DFT-027: group-registered tasks can be cancelled together by abort_group.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import bg


class TestSpawn(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bg._bg_tasks.clear()
        bg._group_tasks.clear()

    async def test_spawn_holds_reference_until_done(self):
        ran = []

        async def work():
            await asyncio.sleep(0)
            ran.append(True)

        task = bg.spawn(work())
        self.assertIn(task, bg._bg_tasks)
        await task
        # done_callback discards it from the holding set
        self.assertNotIn(task, bg._bg_tasks)
        self.assertEqual(ran, [True])

    async def test_spawn_logs_exception_without_propagating(self):
        async def boom():
            raise RuntimeError("kaboom")

        task = bg.spawn(boom())
        with self.assertLogs("core.bg", level="ERROR") as cm:
            with self.assertRaises(RuntimeError):
                await task
        self.assertTrue(any("kaboom" in line for line in cm.output))


class TestGroupAbort(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bg._bg_tasks.clear()
        bg._group_tasks.clear()

    async def test_spawn_group_registers_under_group(self):
        async def slow():
            await asyncio.sleep(5)

        task = bg.spawn_group(7, slow())
        self.assertIn(task, bg._group_tasks.get(7, set()))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_abort_group_cancels_all_and_counts(self):
        async def slow():
            await asyncio.sleep(5)

        t1 = bg.spawn_group(1, slow())
        t2 = bg.spawn_group(1, slow())
        other = bg.spawn_group(2, slow())
        await asyncio.sleep(0)  # let them start

        n = bg.abort_group(1)
        self.assertEqual(n, 2)

        for t in (t1, t2):
            with self.assertRaises(asyncio.CancelledError):
                await t
        self.assertFalse(other.done())

        other.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await other

    async def test_done_task_removed_from_group_bucket(self):
        async def quick():
            return "ok"

        task = bg.spawn_group(3, quick())
        await task
        # bucket emptied → group key removed
        self.assertNotIn(3, bg._group_tasks)

    async def test_abort_group_unknown_returns_zero(self):
        self.assertEqual(bg.abort_group(999), 0)


if __name__ == "__main__":
    unittest.main()
