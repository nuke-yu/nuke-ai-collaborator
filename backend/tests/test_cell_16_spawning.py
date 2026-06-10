"""CELL-16: Multi-process spawning unit tests."""
import asyncio
import os
import sys
import unittest
import tempfile
from unittest.mock import MagicMock, patch

# Ensure the backend directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import ipc
from runtime.supervisor import Supervisor

class TestCell16Spawning(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Unique per test: these tests share a pid, and a reused IPC addr lets a
        # not-yet-reaped subprocess from another test reconnect and pollute the
        # supervisor's worker set (flaky under randomized order + the collector).
        import uuid
        self.addr = ipc.make_addr(f"test_spawn_{os.getpid()}_{uuid.uuid4().hex[:8]}")

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
        # Keep the spawned mcp-collector from launching real MCP servers (npx) in tests.
        env["MCP_SERVERS_CONFIG"] = "/nonexistent/mcp_servers.json"

        # Patching sys.executable is hard, we just let it run.
        # We spawn 2 workers (+ the cross-group mcp-collector).
        sup = Supervisor(self.addr, num_workers=2)

        # We use a small hack: since Supervisor._spawn_workers uses sys.executable,
        # we need to make sure the environment is correct.
        with patch.dict(os.environ, env):
            await sup.start()

            try:
                # Wait for the 2 named workers to connect (the collector connects
                # too — 3 cold subprocesses contend at startup, so allow headroom).
                for _ in range(500): # 10 seconds max
                    if "w0" in sup._workers and "w1" in sup._workers:
                        break
                    await asyncio.sleep(0.02)

                self.assertIn("w0", sup._workers)
                self.assertIn("w1", sup._workers)
                # 2 workers + 1 mcp-collector process spawned.
                self.assertEqual(len(sup._processes), 3)

            finally:
                await sup.stop()

    async def test_supervisor_cleanup_on_stop(self):
        sup = Supervisor(self.addr, num_workers=1)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
        env["MCP_SERVERS_CONFIG"] = "/nonexistent/mcp_servers.json"

        with patch.dict(os.environ, env):
            await sup.start()
            await asyncio.sleep(0.2)  # let monitor tasks spin up
            # 1 worker + 1 mcp-collector → 2 monitor tasks
            self.assertEqual(len(sup._monitor_tasks), 2)

            await sup.stop()
            self.assertEqual(len(sup._processes), 0)
            self.assertEqual(len(sup._monitor_tasks), 0)

if __name__ == "__main__":
    unittest.main()
