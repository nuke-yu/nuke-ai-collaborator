"""Tests for the MCP collector process (cross-group MCP owner on the bus).

Drives a real MCPCollector against a minimal in-test supervisor over the real
ipc transport: validates HELLO registration, schema push, and the
MCP_CALL → execute(pre_authorized) → MCP_RESULT round-trip with correlation.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import ipc
from runtime.mcp_collector import MCPCollector
from executors.tool_router import ToolRouter
from executors.base import ToolDef


class _FakeMcpProvider:
    provider_id = "mcp:fake"

    def discover_tools(self):
        return [ToolDef(name="fake__do", description="x", parameters={})]

    def can_handle(self, name):
        return name.startswith("fake__")

    async def execute(self, name, arguments, context):
        # echo back whether the collector marked the call pre-authorized
        return (f"ran:{name}:pre={context.get('_pre_authorized')}", False)


class TestMCPCollectorRoundTrip(unittest.IsolatedAsyncioTestCase):

    async def test_hello_schema_push_and_call_roundtrip(self):
        addr = ipc.make_addr(f"coll_{os.getpid()}")
        got = {"hello": None, "schemas": None, "result": None}
        coll_writer = {}
        schemas_got = asyncio.Event()
        result_got = asyncio.Event()

        async def on_conn(reader, writer):
            got["hello"] = await ipc.recv_msg(reader)
            coll_writer["w"] = writer
            try:
                while True:
                    frame = await ipc.recv_msg(reader)
                    if frame is None:
                        break
                    if frame.get("type") == ipc.protocol.MCP_SCHEMAS:
                        got["schemas"] = frame["payload"]["schemas"]
                        schemas_got.set()
                    elif frame.get("type") == ipc.protocol.MCP_RESULT:
                        got["result"] = frame
                        result_got.set()
            except (asyncio.IncompleteReadError, ConnectionResetError):
                pass

        server = await ipc.serve(addr, on_conn)

        coll = MCPCollector(addr)

        async def _fake_init():
            coll._router = ToolRouter()
            coll._router.register_provider(_FakeMcpProvider())
        coll._init_providers = _fake_init

        run_task = asyncio.create_task(coll.run())
        try:
            # HELLO identifies as the collector
            await asyncio.wait_for(schemas_got.wait(), 2)
            self.assertEqual(got["hello"]["worker_id"], ipc.protocol.MCP_COLLECTOR_ID)
            # schema push includes the fake MCP tool
            self.assertTrue(any(s["function"]["name"] == "fake__do" for s in got["schemas"]))

            # supervisor relays an MCP_CALL → collector executes → MCP_RESULT
            await ipc.send_msg(coll_writer["w"], ipc.protocol.envelope(
                ipc.protocol.MCP_CALL, group_id=1, trace_id="t1",
                request_id="r1", origin_worker_id="w0",
                tool="fake__do", arguments={"a": 1},
            ))
            await asyncio.wait_for(result_got.wait(), 2)
            res = got["result"]
            self.assertEqual(res["request_id"], "r1")
            self.assertEqual(res["origin_worker_id"], "w0")
            self.assertFalse(res["is_error"])
            self.assertIn("ran:fake__do:pre=True", res["result"])  # pre-authorized
        finally:
            run_task.cancel()
            try:
                await run_task
            except (asyncio.CancelledError, Exception):
                pass
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), 1)
            except Exception:
                pass


class TestCollectorSchemaAnnotation(unittest.TestCase):
    """Per-server HIL config travels to workers: the collector annotates each
    pushed schema with the owning provider's needs_approval flag."""

    def test_schemas_annotated_with_approval_flags(self):
        from runtime.mcp_collector import MCPCollector

        class _P:
            provider_id = "mcp:fake"
            def discover_tools(self):
                return [ToolDef(name="fake__do", description="", parameters={}),
                        ToolDef(name="fake__read", description="", parameters={})]
            def can_handle(self, n): return n.startswith("fake__")
            async def execute(self, n, a, c): return ("", False)
            def approval_flags(self):
                return {"fake__do": True, "fake__read": False}

        coll = MCPCollector("x")
        coll._router = ToolRouter()
        coll._router.register_provider(_P())
        by = {s["function"]["name"]: s.get("needs_approval") for s in coll._schemas()}
        self.assertIs(by["fake__do"], True)
        self.assertIs(by["fake__read"], False)


class TestCollectorAuth(unittest.IsolatedAsyncioTestCase):

    async def test_auth_start_unknown_server_errors(self):
        from runtime.mcp_collector import MCPCollector
        from unittest.mock import patch
        coll = MCPCollector("x")
        coll._router = ToolRouter()           # no providers → server not found
        coll._writer = object()
        sent = []
        async def fake_send(w, m):
            sent.append(m)
        with patch("runtime.ipc.send_msg", new=fake_send):
            await coll._handle_auth_start({
                "request_id": "a1", "origin_worker_id": "w0",
                "server": "nope", "group_id": 1,
            })
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], ipc.protocol.MCP_RESULT)
        self.assertTrue(sent[0]["is_error"])
        self.assertEqual(sent[0]["request_id"], "a1")
        self.assertEqual(sent[0]["origin_worker_id"], "w0")

    async def test_callback_url_uses_env(self):
        import os
        from runtime.mcp_collector import MCPCollector
        coll = MCPCollector("x")
        self.assertEqual(coll._callback_url(), "http://127.0.0.1:8000/mcp/oauth/callback")
        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://demo.example.com/"}):
            self.assertEqual(coll._callback_url(), "https://demo.example.com/mcp/oauth/callback")


if __name__ == "__main__":
    unittest.main()

