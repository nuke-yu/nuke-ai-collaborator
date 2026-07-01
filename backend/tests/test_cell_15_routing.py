"""CELL-15: Persistent routing unit tests."""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

# Ensure the backend directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import db.writer as _writer
from runtime.supervisor import Supervisor

class TestCell15Routing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.central = tempfile.mktemp(suffix="_central.db")
        self._orig_db, self._orig_w = db.DB_PATH, _writer.DB_PATH
        # Monkeypatch DB_PATH to use our temporary central database
        db.DB_PATH = self.central
        _writer.DB_PATH = self.central
        
        # Initialize central schema (CELL-05 logic)
        from db.schema_split import init_central_db
        await init_central_db(self.central)
        
        # Insert a group with a specific worker
        async with db.write_connect() as c:
            await c.execute(
                "INSERT INTO groups (id, name, assigned_worker_id) VALUES (?, ?, ?)",
                (7, "Group 7", "w_seven")
            )
            await c.commit()

    async def asyncTearDown(self):
        await db.aclose_writer()
        db.DB_PATH, _writer.DB_PATH = self._orig_db, self._orig_w
        for p in (self.central,):
            for s in ("", "-wal", "-shm"):
                try:
                    os.unlink(p + s)
                except FileNotFoundError:
                    pass

    async def test_supervisor_routes_using_db(self):
        sup = Supervisor("dummy_addr")
        
        # 1. Initial route (cold cache)
        wid = await sup._default_route(7)
        self.assertEqual(wid, "w_seven")
        self.assertEqual(sup._routing_cache[7][0], "w_seven")

        # 2. Change DB but check cache (warm cache)
        async with db.write_connect() as c:
            await c.execute("UPDATE groups SET assigned_worker_id='w_new' WHERE id=7")
            await c.commit()
        
        wid_cached = await sup._default_route(7)
        self.assertEqual(wid_cached, "w_seven", "Should hit cache and return the old worker")

        # 3. New group (cold cache)
        async with db.write_connect() as c:
            await c.execute(
                "INSERT INTO groups (id, name, assigned_worker_id) VALUES (?, ?, ?)",
                (9, "Group 9", "w_nine")
            )
            await c.commit()
        
        wid_9 = await sup._default_route(9)
        self.assertEqual(wid_9, "w_nine")

    async def test_route_fallback_to_w0(self):
        sup = Supervisor("dummy_addr")
        # Non-existent group should fallback to w0 (num_workers unset → single worker)
        wid = await sup._default_route(999)
        self.assertEqual(wid, "w0")

    async def test_unassigned_group_spreads_across_workers(self):
        # A group with NULL assigned_worker_id must be spread deterministically by
        # id across the configured workers, NOT pile onto w0 (the hotspot bug).
        async with db.write_connect() as c:
            await c.execute("INSERT INTO groups (id, name) VALUES (?, ?)", (10, "G10"))
            await c.execute("INSERT INTO groups (id, name) VALUES (?, ?)", (11, "G11"))
            await c.execute("INSERT INTO groups (id, name) VALUES (?, ?)", (12, "G12"))
            await c.commit()
        sup = Supervisor("dummy_addr", num_workers=4)
        self.assertEqual(await sup._default_route(10), "w2")   # 10 % 4
        self.assertEqual(await sup._default_route(11), "w3")   # 11 % 4
        self.assertEqual(await sup._default_route(12), "w0")   # 12 % 4

    async def test_unassigned_nonexistent_group_spreads_too(self):
        # Even a group with no DB row should spread by id (not hardcoded w0).
        sup = Supervisor("dummy_addr", num_workers=4)
        self.assertEqual(await sup._default_route(13), "w1")   # 13 % 4

    async def test_explicit_assignment_overrides_modulo(self):
        # An explicit assigned_worker_id always wins over the modulo fallback.
        sup = Supervisor("dummy_addr", num_workers=4)
        wid = await sup._default_route(7)   # group 7 → "w_seven" from setUp
        self.assertEqual(wid, "w_seven")

    async def test_send_to_worker_integration(self):
        # Test that send_to_worker correctly uses the async route
        sup = Supervisor("dummy_addr")
        # Mock the worker connection
        mock_writer = AsyncMock()
        sup._workers["w_seven"] = mock_writer
        
        from runtime import ipc
        with patch("runtime.ipc.send_msg", new_callable=AsyncMock) as mock_send:
            await sup.send_to_worker(7, {"type": "test"})
            mock_send.assert_called_once_with(mock_writer, {"type": "test"})

    async def test_send_to_worker_refreshes_stale_cache_after_reassignment(self):
        sup = Supervisor("dummy_addr")
        self.assertEqual(await sup._default_route(7), "w_seven")

        async with db.write_connect() as c:
            await c.execute("UPDATE groups SET assigned_worker_id='w_new' WHERE id=7")
            await c.commit()

        mock_writer = AsyncMock()
        sup._workers["w_new"] = mock_writer

        with patch("runtime.ipc.send_msg", new_callable=AsyncMock) as mock_send:
            await sup.send_to_worker(7, {"type": "refresh"})
            mock_send.assert_called_once_with(mock_writer, {"type": "refresh"})
        self.assertEqual(sup._routing_cache[7][0], "w_new")

    async def test_drop_worker_routes_clears_all_cached_groups_for_worker(self):
        sup = Supervisor("dummy_addr")
        sup._routing_cache[7] = ("w1", 9999999999.0)
        sup._routing_cache[8] = ("w2", 9999999999.0)
        sup._routing_cache[9] = ("w1", 9999999999.0)

        sup._drop_worker_routes("w1")

        self.assertNotIn(7, sup._routing_cache)
        self.assertNotIn(9, sup._routing_cache)
        self.assertEqual(sup._routing_cache[8][0], "w2")

    async def test_stop_waits_for_worker_connections_to_close(self):
        sup = Supervisor("dummy_addr")

        class DummyWriter:
            def __init__(self):
                self.closed = False
                self.waited = False

            def close(self):
                self.closed = True

            async def wait_closed(self):
                self.waited = True

        writer = DummyWriter()
        sup._workers["w1"] = writer

        await sup.stop()

        self.assertTrue(writer.closed)
        self.assertTrue(writer.waited)
        self.assertEqual(sup._workers, {})

if __name__ == "__main__":
    unittest.main()
