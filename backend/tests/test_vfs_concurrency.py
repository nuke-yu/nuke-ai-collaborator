import os
import sys
import unittest
import asyncio
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workspace import write_file, read_file, bot_workspace
from skills.constants import WORKSPACE_ROOT

class TestVFSConcurrency(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Clean up test workspace
        self.bot_id = 999
        self.test_ws = bot_workspace(self.bot_id)
        self.test_file = "concurrency_test.txt"
        p = self.test_ws / self.test_file
        if p.exists():
            p.unlink()

    async def test_concurrent_writes_are_atomic(self):
        # Simulate multiple concurrent writes to the same file
        num_writes = 20
        tasks = []
        for i in range(num_writes):
            content = f"content_{i}\n"
            tasks.append(write_file(self.bot_id, self.test_file, content))
        
        results = await asyncio.gather(*tasks)
        
        # Verify all writes returned success
        for r in results:
            self.assertIn("已写入", r)
        
        # Read the file and ensure it contains exactly one of the versions 
        # (since each write overwrites the whole file in the current implementation,
        # the lock ensures they don't interleave or corrupt).
        final_content = await read_file(self.bot_id, self.test_file)
        self.assertTrue(final_content.startswith("content_"))
        self.assertEqual(final_content.count("\n"), 1)

    async def test_read_during_write_is_consistent(self):
        # Start a heavy write then immediate read
        large_content = "x" * 1000000 # 1MB
        
        # We use a wrapper to check timing if needed, but the lock should force sequence
        write_task = asyncio.create_task(write_file(self.bot_id, self.test_file, large_content))
        read_task = asyncio.create_task(read_file(self.bot_id, self.test_file))
        
        await asyncio.gather(write_task, read_task)
        
        final_content = await read_file(self.bot_id, self.test_file)
        self.assertEqual(len(final_content), 1000000)

if __name__ == "__main__":
    unittest.main()
