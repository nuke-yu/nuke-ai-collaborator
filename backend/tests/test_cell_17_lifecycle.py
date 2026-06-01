"""CELL-17: Group lifecycle (hydration/eviction) unit tests."""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

# Ensure the backend directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import db.writer as _writer
from runtime.lifecycle import LifecycleManager

class TestCell17Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_root = None
        try:
            from skills import constants
            self._orig_root = constants.WORKSPACE_ROOT
            constants.WORKSPACE_ROOT = self.tmpdir
        except Exception:
            pass

    async def asyncTearDown(self):
        await db.aclose_writer()
        try:
            from skills import constants
            if self._orig_root:
                constants.WORKSPACE_ROOT = self._orig_root
        except Exception:
            pass
        
        # Cleanup temp dir
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_lazy_hydration_creates_db(self):
        lm = LifecycleManager()
        gid = 101
        
        # Hydrate
        path = await lm.hydrate(gid)
        self.assertTrue(os.path.exists(path))
        
        # Verify schema
        async with db.connect(path) as conn:
            async with conn.execute("SELECT name FROM sqlite_master WHERE name='messages'") as cur:
                self.assertIsNotNone(await cur.fetchone())

    async def test_lru_eviction_closes_writer(self):
        lm = LifecycleManager(max_groups=2)
        
        # 1. Hydrate 2 groups
        await lm.hydrate(1)
        await lm.hydrate(2)
        
        # Ensure writer is open for group 1
        path1 = await lm.hydrate(1)
        async with db.write_connect(path1) as c: # hydrate 1 again to make it warm
             pass
        
        # 2. Hydrate a 3rd group -> 1 should be evicted (LRU)
        # Note: we need to make sure 1 is the oldest. 
        # In asyncSetUp we hydrated 1 then 2. So 1 is oldest.
        # Wait, LM.hydrate(1) in step 1 made 1 the NEWEST.
        # Let's be explicit:
        await lm.hydrate(1) # [1]
        await lm.hydrate(2) # [1, 2]
        
        # Verify both in active
        self.assertEqual(len(lm._active_groups), 2)
        self.assertIn(1, lm._active_groups)
        
        # Mock aclose_writer to verify call
        with patch("db.aclose_writer", new_callable=AsyncMock) as mock_close:
            await lm.hydrate(3) # [2, 3], 1 evicted
            self.assertEqual(len(lm._active_groups), 2)
            self.assertNotIn(1, lm._active_groups)
            # aclose_writer should be called for group 1's path
            from runtime.dbpaths import group_db_path
            mock_close.assert_called_with(group_db_path(1))

    async def test_memory_state_cleared_on_eviction(self):
        lm = LifecycleManager(max_groups=1)
        await lm.hydrate(1)
        
        # Mock states
        mock_rd = MagicMock()
        mock_rd._last_tickets = {1: {"T-1": "done"}}
        
        mock_perm = MagicMock()
        mock_perm.cancel_pending_for_group = MagicMock()
        
        with patch("core.orchestration.rd_manager.rd_manager", mock_rd), \
             patch("permissions.engine.cancel_pending_for_group", mock_perm.cancel_pending_for_group):
            
            await lm.hydrate(2) # 1 evicted
            
            self.assertNotIn(1, mock_rd._last_tickets)
            mock_perm.cancel_pending_for_group.assert_called_once_with(1)

if __name__ == "__main__":
    unittest.main()
