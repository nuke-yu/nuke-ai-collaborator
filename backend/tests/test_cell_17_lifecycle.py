"""CELL-17: Group lifecycle (hydration/eviction) unit tests."""
import asyncio
import os
import sys
import tempfile
import time
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

    async def test_inactivity_eviction_sweeps_inactive_group(self):
        lm = LifecycleManager()
        await lm.hydrate(1)
        
        # Override GROUP_INACTIVITY_TIMEOUT env and mock last active time
        with patch.dict(os.environ, {"GROUP_INACTIVITY_TIMEOUT": "0.1"}):
            # Wait 0.2s to exceed timeout
            await asyncio.sleep(0.2)
            
            # Mock aclose_writer to avoid actual DB close exception during sweep
            with patch("db.aclose_writer", new_callable=AsyncMock) as mock_close:
                await lm.sweep_inactive_groups()
                self.assertNotIn(1, lm._active_groups)
                mock_close.assert_called_once()
        await lm.shutdown()

    async def test_inactivity_eviction_skips_group_with_active_tasks(self):
        lm = LifecycleManager()
        await lm.hydrate(1)
        
        # Mock background task for group 1
        mock_task = MagicMock()
        mock_task.done.return_value = False
        
        from core import bg
        bg._group_tasks[1] = {mock_task}
        
        try:
            with patch.dict(os.environ, {"GROUP_INACTIVITY_TIMEOUT": "0.1"}):
                await asyncio.sleep(0.2)
                with patch("db.aclose_writer", new_callable=AsyncMock) as mock_close:
                    await lm.sweep_inactive_groups()
                    # Should NOT be evicted because of the active task
                    self.assertIn(1, lm._active_groups)
                    mock_close.assert_not_called()
        finally:
            bg._group_tasks.pop(1, None)
        await lm.shutdown()

    async def test_prune_resources(self):
        lm = LifecycleManager()
        
        # Setup mock directory structures
        from pathlib import Path
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        old_log = log_dir / "worker-test_prune.log"
        old_log.touch()
        
        from skills.constants import WORKSPACE_ROOT
        temp_dir = Path(WORKSPACE_ROOT) / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        old_temp = temp_dir / "old_file.txt"
        old_temp.touch()
        
        # Modify mtime to be 15 days ago
        fifteen_days_ago = time.time() - (15 * 24 * 3600)
        os.utime(old_log, (fifteen_days_ago, fifteen_days_ago))
        os.utime(old_temp, (fifteen_days_ago, fifteen_days_ago))
        
        try:
            await lm.prune_resources()
            
            # Assert files deleted
            self.assertFalse(old_log.exists())
            self.assertFalse(old_temp.exists())
        finally:
            old_log.unlink(missing_ok=True)
            old_temp.unlink(missing_ok=True)
            if temp_dir.exists():
                try: temp_dir.rmdir()
                except Exception: pass
        await lm.shutdown()

    async def test_shutdown_reuses_full_evict_cleanup(self):
        lm = LifecycleManager()
        lm._active_groups[1] = time.time()
        lm._active_groups[2] = time.time()

        with patch.object(lm, "_do_evict", new_callable=AsyncMock) as mock_evict:
            await lm.shutdown()

        self.assertEqual(mock_evict.await_count, 2)
        mock_evict.assert_any_await(1)
        mock_evict.assert_any_await(2)
        self.assertEqual(lm._active_groups, {})

    async def test_shutdown_waits_for_evictor_task_cancellation(self):
        lm = LifecycleManager()
        cancelled = asyncio.Event()

        async def _evictor():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        lm._evictor_task = asyncio.create_task(_evictor())
        await asyncio.sleep(0)

        await lm.shutdown()

        self.assertTrue(cancelled.is_set())
        self.assertIsNone(lm._evictor_task)

    async def test_evict_releases_manager_lock_before_cleanup(self):
        lm = LifecycleManager()
        lm._active_groups[1] = time.time()
        lock_states = []

        async def fake_evict(gid):
            lock_states.append(lm._lock.locked())

        with patch.object(lm, "_do_evict", new=fake_evict):
            await lm.evict(1)

        self.assertEqual(lock_states, [False])
        self.assertNotIn(1, lm._active_groups)

    async def test_sweep_releases_manager_lock_before_cleanup(self):
        lm = LifecycleManager()
        lm._active_groups[1] = time.time() - 10
        lock_states = []

        async def fake_evict(gid):
            lock_states.append(lm._lock.locked())

        with patch.dict(os.environ, {"GROUP_INACTIVITY_TIMEOUT": "0"}):
            with patch.object(lm, "_do_evict", new=fake_evict):
                await lm.sweep_inactive_groups()

        self.assertEqual(lock_states, [False])
        self.assertNotIn(1, lm._active_groups)

    async def test_hydrate_releases_manager_lock_before_schema_init(self):
        lm = LifecycleManager()
        lock_states = []

        async def fake_init_group_db(path):
            lock_states.append(lm._lock.locked())

        with patch("db.schema_split.init_group_db", new=fake_init_group_db), \
             patch("db.migrations.run_migrations", new_callable=AsyncMock), \
             patch("core.orchestration.rd_manager.rd_manager.check_board", new_callable=AsyncMock), \
             patch("core.runner.resume_workflows", new_callable=AsyncMock), \
             patch("sessions.recover_all", new_callable=AsyncMock):
            await lm.hydrate(1)

        self.assertEqual(lock_states, [False])
        await lm.shutdown()

    async def test_concurrent_hydrate_same_group_reuses_single_inflight_operation(self):
        lm = LifecycleManager()
        started = asyncio.Event()
        release = asyncio.Event()
        init_calls = 0

        async def fake_init_group_db(path):
            nonlocal init_calls
            init_calls += 1
            started.set()
            await release.wait()

        with patch("db.schema_split.init_group_db", new=fake_init_group_db), \
             patch("db.migrations.run_migrations", new_callable=AsyncMock), \
             patch("core.orchestration.rd_manager.rd_manager.check_board", new_callable=AsyncMock), \
             patch("core.runner.resume_workflows", new_callable=AsyncMock), \
             patch("sessions.recover_all", new_callable=AsyncMock):
            first = asyncio.create_task(lm.hydrate(1))
            await started.wait()
            second = asyncio.create_task(lm.hydrate(1))
            await asyncio.sleep(0)
            self.assertEqual(init_calls, 1)
            release.set()
            first_path, second_path = await asyncio.gather(first, second)

        self.assertEqual(first_path, second_path)
        self.assertEqual(init_calls, 1)
        await lm.shutdown()

    async def test_evict_waits_for_inflight_hydration_then_cleans_group(self):
        lm = LifecycleManager()
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_init_group_db(path):
            started.set()
            await release.wait()

        with patch("db.schema_split.init_group_db", new=fake_init_group_db), \
             patch("db.migrations.run_migrations", new_callable=AsyncMock), \
             patch("core.orchestration.rd_manager.rd_manager.check_board", new_callable=AsyncMock), \
             patch("core.runner.resume_workflows", new_callable=AsyncMock), \
             patch("sessions.recover_all", new_callable=AsyncMock), \
             patch.object(lm, "_do_evict", new_callable=AsyncMock) as mock_evict:
            hydrate_task = asyncio.create_task(lm.hydrate(1))
            await started.wait()
            evict_task = asyncio.create_task(lm.evict(1))
            await asyncio.sleep(0)
            self.assertFalse(evict_task.done())
            release.set()
            await asyncio.gather(hydrate_task, evict_task)

        mock_evict.assert_awaited_once_with(1)
        self.assertNotIn(1, lm._active_groups)

    async def test_concurrent_sweep_and_explicit_evict_share_single_cleanup(self):
        lm = LifecycleManager()
        lm._active_groups[1] = time.time() - 10
        started = asyncio.Event()
        release = asyncio.Event()
        evict_calls = 0

        async def fake_evict(gid):
            nonlocal evict_calls
            evict_calls += 1
            started.set()
            await release.wait()

        with patch.dict(os.environ, {"GROUP_INACTIVITY_TIMEOUT": "0"}):
            with patch.object(lm, "_do_evict", new=fake_evict):
                sweep_task = asyncio.create_task(lm.sweep_inactive_groups())
                await started.wait()
                evict_task = asyncio.create_task(lm.evict(1))
                await asyncio.sleep(0)
                self.assertFalse(evict_task.done())
                release.set()
                await asyncio.gather(sweep_task, evict_task)

        self.assertEqual(evict_calls, 1)
        self.assertNotIn(1, lm._active_groups)

    async def test_shutdown_waits_for_inflight_hydration_and_prevents_reactivation(self):
        lm = LifecycleManager()
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_init_group_db(path):
            started.set()
            await release.wait()

        with patch("db.schema_split.init_group_db", new=fake_init_group_db), \
             patch("db.migrations.run_migrations", new_callable=AsyncMock), \
             patch("core.orchestration.rd_manager.rd_manager.check_board", new_callable=AsyncMock), \
             patch("core.runner.resume_workflows", new_callable=AsyncMock), \
             patch("sessions.recover_all", new_callable=AsyncMock):
            hydrate_task = asyncio.create_task(lm.hydrate(1))
            await started.wait()
            shutdown_task = asyncio.create_task(lm.shutdown())
            await asyncio.sleep(0)
            self.assertFalse(shutdown_task.done())
            release.set()
            with self.assertRaises(RuntimeError):
                await hydrate_task
            await shutdown_task

        self.assertNotIn(1, lm._active_groups)
        self.assertEqual(lm._hydrating, {})

    async def test_shutdown_waits_for_inflight_evictions(self):
        lm = LifecycleManager()
        lm._active_groups[1] = time.time() - 10
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_evict(gid):
            started.set()
            await release.wait()

        with patch.dict(os.environ, {"GROUP_INACTIVITY_TIMEOUT": "0"}):
            with patch.object(lm, "_do_evict", new=fake_evict):
                sweep_task = asyncio.create_task(lm.sweep_inactive_groups())
                await started.wait()
                shutdown_task = asyncio.create_task(lm.shutdown())
                await asyncio.sleep(0)
                self.assertFalse(shutdown_task.done())
                release.set()
                await asyncio.gather(sweep_task, shutdown_task)

        self.assertEqual(lm._evicting, {})

if __name__ == "__main__":
    unittest.main()
