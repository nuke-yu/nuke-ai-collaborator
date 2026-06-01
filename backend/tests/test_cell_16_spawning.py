"""CELL-16: Multi-process spawning unit tests."""
import asyncio
import os
import sys
import unittest
import tempfile
from unittest.mock import MagicMock

# Ensure the backend directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import ipc
from runtime.supervisor import Supervisor

class TestCell16Spawning(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addr = ipc.make_addr(f"test_spawn_{os.getpid()}")

    async def asyncTearDown(self):
        if os.path.exists(self.addr):
            try:
                os.unlink(self.addr)
            except Exception:
                pass

    async def test_supervisor_spawns_workers(self):
        # We need to set PYTHONPATH so the spawned workers can find the 'runtime' module
        env = os.environ.copy()
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
        
        # Patching sys.executable is hard, we just let it run.
        # We spawn 2 workers.
        sup = Supervisor(self.addr, num_workers=2)
        
        # We use a small hack: since Supervisor._spawn_workers uses sys.executable,
        # we need to make sure the environment is correct.
        with patch.dict(os.environ, env):
            await sup.start()
            
            try:
                # Wait for workers to connect
                for _ in range(200): # 4 seconds max
                    if len(sup._workers) >= 2:
                        break
                    await asyncio.sleep(0.02)
                
                self.assertEqual(len(sup._workers), 2, f"Expected 2 workers to connect, found {list(sup._workers.keys())}")
                self.assertIn("w0", sup._workers)
                self.assertIn("w1", sup._workers)
                self.assertEqual(len(sup._processes), 2)
                
            finally:
                await sup.stop()

    async def test_supervisor_cleanup_on_stop(self):
        sup = Supervisor(self.addr, num_workers=1)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
        
        with patch.dict(os.environ, env):
            await sup.start()
            self.assertEqual(len(sup._processes), 1)
            proc = sup._processes[0]
            self.assertIsNone(proc.returncode) # Still running
            
            await sup.stop()
            self.assertEqual(len(sup._processes), 0)
            # Process should be terminated
            await asyncio.sleep(0.1)
            self.assertIsNotNone(proc.returncode)

if __name__ == "__main__":
    from unittest.mock import patch
    unittest.main()
