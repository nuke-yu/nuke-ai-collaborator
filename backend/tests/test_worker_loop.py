"""CELL-10: Worker runtime loop.

Drives the real Worker + real ipc transport against a minimal in-test supervisor:
a downstream user_message is routed to the dispatch handler, whose bus events are
forwarded upstream as `broadcast` frames carrying the right group_id/payload.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import ipc
from runtime.worker import Worker
from executors.mcp_bridge import bridge as global_bridge
from bus.engine import EventBus


class TestWorkerLoop(unittest.IsolatedAsyncioTestCase):
    async def test_connect_can_skip_startup_group_hydration(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None, hydrate_assigned_groups=False)

        class DummyWriter:
            async def drain(self): pass
            def close(self): pass

        async def fake_connect(addr):
            return object(), DummyWriter()

        async def fake_send_msg(writer, msg):
            return None

        with patch("runtime.worker.ipc.connect", new=fake_connect), \
             patch("runtime.worker.ipc.send_msg", new=fake_send_msg):
            await worker.connect()

        self.assertIsNone(worker._hydration_task)
        await worker.close()

    async def test_close_waits_for_background_tasks_and_writer_shutdown(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)

        cancelled = []

        async def _hold(label):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(label)
                raise

        worker._report_task = asyncio.create_task(_hold("report"))
        worker._upstream_task = asyncio.create_task(_hold("upstream"))
        worker._recap_task = asyncio.create_task(_hold("recap"))
        worker._compaction_task = asyncio.create_task(_hold("compaction"))
        worker._hydration_task = asyncio.create_task(_hold("hydration"))
        await asyncio.sleep(0)

        class DummyWriter:
            def __init__(self):
                self.closed = False
                self.waited = False

            def close(self):
                self.closed = True

            async def wait_closed(self):
                self.waited = True

        writer = DummyWriter()
        worker._writer = writer

        await worker.close()

        self.assertCountEqual(cancelled, ["report", "upstream", "recap", "compaction", "hydration"])
        self.assertTrue(writer.closed)
        self.assertTrue(writer.waited)
        self.assertIsNone(worker._writer)

    async def test_close_resets_mcp_bridge_schemas(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)
        saved = list(global_bridge.schemas)
        try:
            global_bridge.set_schemas([
                {"function": {"name": "fs__read_file", "description": "", "parameters": {}}}
            ])
            self.assertIsNotNone(global_bridge.schema_for("fs__read_file"))

            await worker.close()

            self.assertEqual(global_bridge.schemas, [])
            self.assertIsNone(global_bridge.schema_for("fs__read_file"))
        finally:
            global_bridge.set_schemas(saved)

    async def test_startup_hydration_task_cancels_during_retry_sleep(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)

        class _Conn:
            async def __aenter__(self):
                raise sqlite3.OperationalError("database is locked")
            async def __aexit__(self, *args):
                return False

        with patch("runtime.worker.db.global_db", return_value=_Conn()):
            task = asyncio.create_task(worker._hydrate_assigned_groups())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_startup_hydration_retries_then_hydrates_assigned_groups(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)
        calls = {"n": 0}
        hydrated = []

        class _Cursor:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def fetchall(self): return [(7,), (9,)]

        class _Conn:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def execute(self, *_args, **_kwargs): return _Cursor()

        def fake_global_db():
            calls["n"] += 1
            if calls["n"] == 1:
                class _LockedConn:
                    async def __aenter__(self):
                        raise sqlite3.OperationalError("database is locked")
                    async def __aexit__(self, *args):
                        return False
                return _LockedConn()
            return _Conn()

        async def fake_hydrate(gid):
            hydrated.append(gid)
            return f"/tmp/group_{gid}.db"

        async def fake_sleep(_delay):
            return None

        with patch("runtime.worker.db.global_db", new=fake_global_db), \
             patch("runtime.worker.asyncio.sleep", new=fake_sleep), \
             patch("runtime.lifecycle.manager.hydrate", new=fake_hydrate):
            await worker._hydrate_assigned_groups()

        self.assertEqual(calls["n"], 2)
        self.assertEqual(hydrated, [7, 9])

    async def test_startup_hydration_continues_after_one_group_fails(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)
        hydrated = []

        class _Cursor:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def fetchall(self): return [(7,), (9,)]

        class _Conn:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def execute(self, *_args, **_kwargs): return _Cursor()

        def fake_global_db():
            return _Conn()

        async def fake_hydrate(gid):
            hydrated.append(gid)
            if gid == 7:
                raise RuntimeError("boom")
            return f"/tmp/group_{gid}.db"

        with patch("runtime.worker.db.global_db", new=fake_global_db), \
             patch("runtime.lifecycle.manager.hydrate", new=fake_hydrate):
            await worker._hydrate_assigned_groups()

        self.assertEqual(hydrated, [7, 9])

    async def test_startup_hydration_does_not_retry_non_transient_db_errors(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)
        sleeps = []

        class _Conn:
            async def __aenter__(self):
                raise sqlite3.OperationalError("disk I/O error")
            async def __aexit__(self, *args):
                return False

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch("runtime.worker.db.global_db", return_value=_Conn()), \
             patch("runtime.worker.asyncio.sleep", new=fake_sleep), \
             patch("runtime.lifecycle.manager.hydrate", new=unittest.mock.AsyncMock()) as mock_hydrate:
            await worker._hydrate_assigned_groups()

        self.assertEqual(sleeps, [])
        mock_hydrate.assert_not_awaited()

    async def test_startup_hydration_gives_up_after_retry_budget(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)
        sleeps = []

        class _Conn:
            async def __aenter__(self):
                raise sqlite3.OperationalError("database is locked")
            async def __aexit__(self, *args):
                return False

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch("runtime.worker.db.global_db", return_value=_Conn()), \
             patch("runtime.worker.asyncio.sleep", new=fake_sleep), \
             patch("runtime.lifecycle.manager.hydrate", new=unittest.mock.AsyncMock()) as mock_hydrate:
            await worker._hydrate_assigned_groups()

        self.assertEqual(len(sleeps), 14)
        mock_hydrate.assert_not_awaited()

    async def test_user_message_to_upstream_broadcast(self):
        addr = ipc.make_addr(f"worker_loop_{os.getpid()}")
        worker_bus = EventBus()
        upstream = []
        dn_writer = {}
        ready = asyncio.Event()
        got_end = asyncio.Event()

        async def on_worker(reader, writer):
            dn_writer["w"] = writer
            ready.set()
            try:
                while True:
                    frame = await ipc.recv_msg(reader)
                    upstream.append(frame)
                    if frame.get("payload", {}).get("type") == "stream_end":
                        got_end.set()
            except (asyncio.IncompleteReadError, ConnectionResetError):
                pass

        server = await ipc.serve(addr, on_worker)

        async def fake_dispatch(msg):
            gid = msg["group_id"]
            await worker_bus.broadcast(gid, {"type": "stream_chunk", "temp_id": "t", "delta": "hi"})
            await worker_bus.broadcast(gid, {"type": "stream_end", "temp_id": "t"})

        worker = Worker("w0", addr, bus=worker_bus, dispatch=fake_dispatch)
        run_task = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(ready.wait(), 5)     # worker connected
            await ipc.send_msg(dn_writer["w"], ipc.protocol.envelope(
                ipc.protocol.USER_MESSAGE, group_id=7, content="hello", trace_id="tr-1"))
            await asyncio.wait_for(got_end.wait(), 5)   # both events forwarded
        finally:
            await worker.close()
            run_task.cancel()
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), 2)
            except Exception:
                pass
            if os.path.exists(addr):
                os.unlink(addr)

        broadcasts = [f for f in upstream if f["type"] == ipc.protocol.BROADCAST]
        self.assertEqual(len(broadcasts), 2)
        self.assertTrue(all(f["group_id"] == 7 for f in broadcasts))
        self.assertEqual(broadcasts[0]["payload"]["type"], "stream_chunk")
        self.assertEqual(broadcasts[0]["payload"]["delta"], "hi")
        self.assertEqual(broadcasts[1]["payload"]["type"], "stream_end")

    async def test_abort_routes_to_handler(self):
        addr = ipc.make_addr(f"worker_abort_{os.getpid()}")
        worker_bus = EventBus()
        ready = asyncio.Event()
        aborted = []
        dn_writer = {}

        async def on_worker(reader, writer):
            dn_writer["w"] = writer
            ready.set()
            try:
                while True:
                    await ipc.recv_msg(reader)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                pass

        server = await ipc.serve(addr, on_worker)
        worker = Worker("w0", addr, bus=worker_bus,
                        dispatch=lambda m: asyncio.sleep(0),
                        on_abort=lambda gid: aborted.append(gid))
        run_task = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(ready.wait(), 5)
            await ipc.send_msg(dn_writer["w"], ipc.protocol.envelope(
                ipc.protocol.ABORT, group_id=42))
            for _ in range(100):
                if aborted:
                    break
                await asyncio.sleep(0.02)
        finally:
            await worker.close()
            run_task.cancel()
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), 2)
            except Exception:
                pass
            if os.path.exists(addr):
                os.unlink(addr)

        self.assertEqual(aborted, [42])

    async def test_upstream_send_failure_does_not_kill_pump(self):
        """C-1: one failing ipc.send_msg must drop only that event, not stop the
        pump — subsequent bus events must still be forwarded upstream."""
        worker_bus = EventBus()
        sent = []

        class FlakyWriter:
            """Stand-in StreamWriter: raises once, then succeeds."""
            def __init__(self):
                self.calls = 0

        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)
        worker._writer = FlakyWriter()
        worker._sub = worker_bus.subscribe_all()

        original = ipc.send_msg
        state = {"n": 0}

        async def flaky_send(writer, msg):
            state["n"] += 1
            if state["n"] == 1:
                raise BrokenPipeError("boom")
            sent.append(msg)

        ipc.send_msg = flaky_send
        pump = asyncio.create_task(worker._pump_upstream())
        try:
            await worker_bus.broadcast(1, {"type": "first"})   # this send raises
            await worker_bus.broadcast(1, {"type": "second"})  # must still go out
            for _ in range(100):
                if sent:
                    break
                await asyncio.sleep(0.02)
        finally:
            ipc.send_msg = original
            pump.cancel()

        # First event dropped (raised), pump survived, second forwarded.
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["payload"]["type"], "second")

    async def test_release_lease_evicts_before_ack(self):
        worker_bus = EventBus()
        worker = Worker("w0", "ignored", bus=worker_bus, dispatch=None)
        worker._writer = object()
        steps = []

        async def fake_evict(gid):
            steps.append(("evict", gid))

        async def fake_send(_writer, msg):
            steps.append(("send", msg["type"], msg["group_id"]))

        with patch("runtime.lifecycle.manager.evict", new=fake_evict), \
             patch("runtime.worker.ipc.send_msg", new=fake_send):
            await worker._handle(ipc.protocol.envelope(
                ipc.protocol.RELEASE_LEASE, group_id=7, trace_id="tr"
            ))

        self.assertEqual(steps, [
            ("evict", 7),
            ("send", ipc.protocol.LEASE_RELEASED, 7),
        ])


if __name__ == "__main__":
    unittest.main()
