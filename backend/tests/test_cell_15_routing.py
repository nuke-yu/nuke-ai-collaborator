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
from runtime import ipc
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

    async def test_stop_logs_worker_close_and_wait_failures(self):
        sup = Supervisor("dummy_addr")

        class DummyWriter:
            def __init__(self):
                self.waited = False

            def close(self):
                raise RuntimeError("close failed")

            async def wait_closed(self):
                self.waited = True
                raise RuntimeError("wait failed")

        writer = DummyWriter()
        sup._workers["w1"] = writer

        with self.assertLogs("runtime.supervisor", level="ERROR") as logs:
            await sup.stop()

        self.assertTrue(writer.waited)
        self.assertEqual(sup._workers, {})
        self.assertTrue(any("failed to close worker w1 connection during stop" in line for line in logs.output))
        self.assertTrue(any("worker w1 wait_closed failed during stop" in line for line in logs.output))

    async def test_stop_logs_subprocess_terminate_and_wait_failures(self):
        sup = Supervisor("dummy_addr")

        class DummyProc:
            def terminate(self):
                raise RuntimeError("terminate failed")

            async def wait(self):
                raise RuntimeError("wait failed")

        sup._processes.append(("w1", DummyProc()))

        with self.assertLogs("runtime.supervisor", level="ERROR") as logs:
            await sup.stop()

        self.assertEqual(sup._processes, [])
        self.assertTrue(any("failed to terminate subprocess w1 during stop" in line for line in logs.output))
        self.assertTrue(any("subprocess w1 wait failed during stop" in line for line in logs.output))

    async def test_stop_logs_server_wait_closed_failure(self):
        sup = Supervisor("dummy_addr")

        class DummyServer:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

            async def wait_closed(self):
                raise RuntimeError("server wait failed")

        server = DummyServer()
        sup._server = server

        with self.assertLogs("runtime.supervisor", level="ERROR") as logs:
            await sup.stop()

        self.assertTrue(server.closed)
        self.assertIsNone(sup._server)
        self.assertTrue(any("server wait_closed failed during stop" in line for line in logs.output))

    async def test_stop_clears_supervisor_runtime_state(self):
        sup = Supervisor("dummy_addr")
        sup._worker_stats["w1"] = {"worker_id": "w1"}
        sup._worker_stats_ts["w1"] = 123.0
        sup._routing_cache[7] = ("w1", 9999999999.0)
        fut = asyncio.get_running_loop().create_future()
        sup._pending_handoffs[7] = ("w1", fut)

        await sup.stop()

        self.assertEqual(sup._worker_stats, {})
        self.assertEqual(sup._worker_stats_ts, {})
        self.assertEqual(sup._routing_cache, {})
        self.assertEqual(sup._pending_handoffs, {})
        self.assertIsNone(sup._server)

    async def test_stop_cancels_pending_handoffs(self):
        sup = Supervisor("dummy_addr")
        fut = asyncio.get_running_loop().create_future()
        sup._pending_handoffs[7] = ("w1", fut)

        await sup.stop()

        self.assertTrue(fut.cancelled())
        self.assertEqual(sup._pending_handoffs, {})

    async def test_send_to_worker_id_failure_drops_stale_worker_state(self):
        sup = Supervisor("dummy_addr")
        writer = AsyncMock()
        sup._workers["w1"] = writer
        sup._worker_stats["w1"] = {"worker_id": "w1"}
        sup._worker_stats_ts["w1"] = 123.0
        sup._routing_cache[7] = ("w1", 9999999999.0)

        with patch("runtime.ipc.send_msg", new_callable=AsyncMock, side_effect=BrokenPipeError("boom")):
            sent = await sup.send_to_worker_id("w1", {"type": "test"})

        self.assertFalse(sent)
        self.assertNotIn("w1", sup._workers)
        self.assertNotIn("w1", sup._worker_stats)
        self.assertNotIn("w1", sup._worker_stats_ts)
        self.assertNotIn(7, sup._routing_cache)

    async def test_send_timeout_closes_writer_and_drops_state(self):
        sup = Supervisor("dummy_addr")

        class Writer:
            closed = False
            waited = False

            def close(self):
                self.closed = True

            async def wait_closed(self):
                self.waited = True

        writer = Writer()
        sup._workers["w1"] = writer

        async def blocked_send(_writer, _msg):
            await asyncio.Event().wait()

        with patch("runtime.ipc.send_msg", new=blocked_send), \
             patch("runtime.supervisor.config.SUPERVISOR_SEND_TIMEOUT", 0.01):
            sent = await sup.send_to_worker_id("w1", {"type": "test"})

        self.assertFalse(sent)
        self.assertTrue(writer.closed)
        self.assertTrue(writer.waited)
        self.assertNotIn("w1", sup._workers)

    async def test_cancelled_send_closes_writer_before_propagating(self):
        sup = Supervisor("dummy_addr")
        started = asyncio.Event()

        class Writer:
            closed = False
            waited = False

            def close(self):
                self.closed = True

            async def wait_closed(self):
                self.waited = True

        writer = Writer()
        sup._workers["w1"] = writer

        async def blocked_send(_writer, _msg):
            started.set()
            await asyncio.Event().wait()

        with patch("runtime.ipc.send_msg", new=blocked_send):
            task = asyncio.create_task(sup.send_to_worker_id("w1", {"type": "test"}))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(writer.closed)
        self.assertTrue(writer.waited)
        self.assertNotIn("w1", sup._workers)

    async def test_wait_closed_sync_failure_does_not_mask_send_failure(self):
        sup = Supervisor("dummy_addr")
        writer = unittest.mock.MagicMock()
        writer.wait_closed.side_effect = RuntimeError("transport already gone")
        sup._workers["w1"] = writer

        with patch("runtime.ipc.send_msg", new_callable=AsyncMock,
                   side_effect=BrokenPipeError("boom")):
            sent = await sup.send_to_worker_id("w1", {"type": "test"})

        self.assertFalse(sent)
        self.assertNotIn("w1", sup._workers)
        writer.close.assert_called_once()

    async def test_worker_reconnect_logs_stale_connection_close_failure(self):
        sup = Supervisor("dummy_addr")
        old_writer = unittest.mock.MagicMock()
        old_writer.close.side_effect = RuntimeError("old close failed")
        new_writer = unittest.mock.MagicMock()
        sup._workers["w1"] = old_writer

        calls = 0

        async def fake_recv(_reader):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"type": ipc.protocol.HELLO, "worker_id": "w1"}
            raise asyncio.IncompleteReadError(partial=b"", expected=1)

        with patch("runtime.supervisor.ipc.recv_msg", new=fake_recv), \
             self.assertLogs("runtime.supervisor", level="ERROR") as logs:
            await sup._on_worker_conn(object(), new_writer)

        self.assertTrue(any("failed to close stale connection for worker w1" in line for line in logs.output))
        self.assertNotIn("w1", sup._workers)

    async def test_worker_connect_drops_registration_when_cached_schema_push_fails(self):
        sup = Supervisor("dummy_addr")
        sup._mcp_schemas = {"type": ipc.protocol.MCP_SCHEMAS, "payload": {"schemas": []}}
        writer = AsyncMock()
        writer.close = unittest.mock.MagicMock()
        reader = object()
        recv_frames = iter([
            {"type": ipc.protocol.HELLO, "worker_id": "w1"},
        ])

        async def fake_recv(_reader):
            try:
                return next(recv_frames)
            except StopIteration:
                raise asyncio.IncompleteReadError(b"", None)

        async def fake_send(_writer, _msg):
            raise BrokenPipeError("boom")

        with patch("runtime.ipc.recv_msg", new=fake_recv), \
             patch("runtime.ipc.send_msg", new=fake_send):
            await sup._on_worker_conn(reader, writer)

        self.assertNotIn("w1", sup._workers)
        writer.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()
