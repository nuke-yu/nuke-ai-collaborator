"""Lifecycle helpers in backend.main."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_mod


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


if __name__ == "__main__":
    unittest.main()
