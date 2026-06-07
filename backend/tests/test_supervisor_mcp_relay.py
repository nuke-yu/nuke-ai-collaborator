"""Supervisor MCP bus relay: worker→collector (MCP_CALL), collector→worker
(MCP_RESULT), and MCP_SCHEMAS cache + fan-out. The supervisor is the bus."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.supervisor import Supervisor
from runtime import ipc

COLL = ipc.protocol.MCP_COLLECTOR_ID


class TestSupervisorMcpRelay(unittest.IsolatedAsyncioTestCase):

    def _sup_with_workers(self, *wids):
        sup = Supervisor(addr="test")
        sup._workers = {wid: object() for wid in wids}
        return sup

    async def _capture(self, sup, frame):
        sent = []
        async def fake_send(writer, msg):
            sent.append((writer, msg))
        with patch("runtime.ipc.send_msg", new=fake_send):
            await sup._on_upstream(frame)
        return sent

    async def test_mcp_call_relayed_to_collector(self):
        sup = self._sup_with_workers("w0", COLL)
        frame = ipc.protocol.envelope(
            ipc.protocol.MCP_CALL, group_id=1, request_id="r1",
            origin_worker_id="w0", tool="fake__do", arguments={},
        )
        sent = await self._capture(sup, frame)
        self.assertEqual(len(sent), 1)
        writer, msg = sent[0]
        self.assertIs(writer, sup._workers[COLL])     # routed to collector
        self.assertEqual(msg["request_id"], "r1")

    async def test_mcp_result_relayed_to_origin_worker(self):
        sup = self._sup_with_workers("w0", COLL)
        frame = ipc.protocol.envelope(
            ipc.protocol.MCP_RESULT, group_id=1, request_id="r1",
            origin_worker_id="w0", result="ok", is_error=False,
        )
        sent = await self._capture(sup, frame)
        self.assertEqual(len(sent), 1)
        self.assertIs(sent[0][0], sup._workers["w0"])  # routed to origin worker

    async def test_mcp_schemas_cached_and_fanned_out(self):
        sup = self._sup_with_workers("w0", "w1", COLL)
        frame = ipc.protocol.envelope(
            ipc.protocol.MCP_SCHEMAS, group_id=0,
            payload={"schemas": [{"function": {"name": "fake__do"}}]},
        )
        sent = await self._capture(sup, frame)
        self.assertEqual(sup._mcp_schemas, frame)            # cached
        targets = {id(w) for w, _ in sent}
        self.assertIn(id(sup._workers["w0"]), targets)       # workers get it
        self.assertIn(id(sup._workers["w1"]), targets)
        self.assertNotIn(id(sup._workers[COLL]), targets)    # collector does NOT

    async def test_mcp_call_collector_down_errors_back_to_worker(self):
        sup = self._sup_with_workers("w0")                   # no collector connected
        frame = ipc.protocol.envelope(
            ipc.protocol.MCP_CALL, group_id=1, request_id="r1",
            origin_worker_id="w0", tool="fake__do", arguments={},
        )
        sent = await self._capture(sup, frame)
        # one error MCP_RESULT routed back to the origin worker (no hang)
        self.assertEqual(len(sent), 1)
        writer, msg = sent[0]
        self.assertIs(writer, sup._workers["w0"])
        self.assertEqual(msg["type"], ipc.protocol.MCP_RESULT)
        self.assertTrue(msg["is_error"])
        self.assertEqual(msg["request_id"], "r1")


if __name__ == "__main__":
    unittest.main()
