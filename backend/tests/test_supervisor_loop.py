"""CELL-12: Supervisor end-to-end routing/fan-out.

Real Supervisor + real Worker over real IPC, same process. A (fake) browser
client sends a user_message; it routes Supervisor → Worker → bus → upstream →
Supervisor → browser. Proves the full cell loop minus the WS termination shell.
"""
import asyncio
import os
import sys
import unittest

async def _async_route_w0(gid):
    return "w0"
    
async def _async_route_missing(gid):
    return "missing"


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import ipc
from runtime.worker import Worker
from runtime.supervisor import Supervisor
from bus.engine import EventBus


class _FakeBrowser:
    def __init__(self):
        self.msgs = []
        self.end = asyncio.Event()

    async def send(self, payload):
        self.msgs.append(payload)
        if payload.get("type") == "stream_end":
            self.end.set()


class TestSupervisorLoop(unittest.IsolatedAsyncioTestCase):
    async def test_browser_to_worker_to_browser(self):
        addr = ipc.make_addr(f"sup_loop_{os.getpid()}")
        sup = Supervisor(addr, route=_async_route_w0)
        await sup.start()

        worker_bus = EventBus()

        async def fake_dispatch(msg):
            gid = msg["group_id"]
            await worker_bus.broadcast(gid, {"type": "stream_chunk", "temp_id": "t", "delta": "hi"})
            await worker_bus.broadcast(gid, {"type": "stream_end", "temp_id": "t"})

        worker = Worker("w0", addr, bus=worker_bus, dispatch=fake_dispatch)
        run_task = asyncio.create_task(worker.run())

        browser = _FakeBrowser()
        sup.register_browser(7, browser)

        try:
            for _ in range(100):                       # wait for worker HELLO
                if "w0" in sup._workers:
                    break
                await asyncio.sleep(0.02)
            self.assertIn("w0", sup._workers)

            await sup.send_to_worker(7, ipc.protocol.envelope(
                ipc.protocol.USER_MESSAGE, group_id=7, content="hello", trace_id="tr"))
            await asyncio.wait_for(browser.end.wait(), 5)
        finally:
            await worker.close()
            run_task.cancel()
            await sup.stop()
            if os.path.exists(addr):
                os.unlink(addr)

        self.assertEqual([m["type"] for m in browser.msgs], ["stream_chunk", "stream_end"])
        self.assertEqual(browser.msgs[0]["delta"], "hi")

    async def test_fanout_only_to_registered_group(self):
        addr = ipc.make_addr(f"sup_fan_{os.getpid()}")
        sup = Supervisor(addr, route=_async_route_w0)
        await sup.start()
        worker_bus = EventBus()

        async def dispatch(msg):
            await worker_bus.broadcast(msg["group_id"], {"type": "stream_end", "temp_id": "t"})

        worker = Worker("w0", addr, bus=worker_bus, dispatch=dispatch)
        run_task = asyncio.create_task(worker.run())

        b7, b9 = _FakeBrowser(), _FakeBrowser()
        sup.register_browser(7, b7)
        sup.register_browser(9, b9)
        try:
            for _ in range(100):
                if "w0" in sup._workers:
                    break
                await asyncio.sleep(0.02)
            await sup.send_to_worker(7, ipc.protocol.envelope(
                ipc.protocol.USER_MESSAGE, group_id=7, content="x"))
            await asyncio.wait_for(b7.end.wait(), 5)
        finally:
            await worker.close()
            run_task.cancel()
            await sup.stop()
            if os.path.exists(addr):
                os.unlink(addr)

        self.assertEqual(len(b7.msgs), 1)        # group 7 got the event
        self.assertEqual(len(b9.msgs), 0)        # group 9 isolated

    async def test_send_without_worker_raises(self):
        addr = ipc.make_addr(f"sup_noworker_{os.getpid()}")
        sup = Supervisor(addr, route=_async_route_missing)
        await sup.start()
        try:
            with self.assertRaises(RuntimeError):
                await sup.send_to_worker(1, {"type": "user_message", "group_id": 1})
        finally:
            await sup.stop()
            if os.path.exists(addr):
                os.unlink(addr)


if __name__ == "__main__":
    unittest.main()
