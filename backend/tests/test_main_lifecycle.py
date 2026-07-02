"""Lifecycle helpers in backend.main."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_mod
from runtime import supervisor as sup_mod


class TestMainLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_and_wait_cleans_up_background_task(self):
        cancelled = []

        async def sleeper():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        task = asyncio.create_task(sleeper())
        await asyncio.sleep(0)

        await main_mod._cancel_and_wait(task)

        self.assertEqual(cancelled, [True])
        self.assertTrue(task.cancelled())

    def test_clear_supervisor_ref_only_clears_matching_instance(self):
        marker = object()
        other = object()

        orig = sup_mod.supervisor
        try:
            sup_mod.supervisor = marker
            main_mod._clear_supervisor_ref(marker)
            self.assertIsNone(sup_mod.supervisor)

            sup_mod.supervisor = marker
            main_mod._clear_supervisor_ref(other)
            self.assertIs(sup_mod.supervisor, marker)
        finally:
            sup_mod.supervisor = orig


if __name__ == "__main__":
    unittest.main()
