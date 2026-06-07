"""Tests for the worker-side MCP bridge + McpProxyProvider (collector topology)."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.mcp_bridge import MCPBridge, bridge as global_bridge
from executors.providers.mcp_proxy import McpProxyProvider


class TestMCPBridge(unittest.IsolatedAsyncioTestCase):

    async def test_request_resolve_roundtrip(self):
        b = MCPBridge()
        captured = {}
        async def send(rid, name, args, gid, tid):
            captured.update(rid=rid, name=name, gid=gid)
        b.install(send, "w0")
        task = asyncio.create_task(
            b.request("fs__read", {"p": 1}, group_id=1, trace_id="t"))
        await asyncio.sleep(0)                       # let it send + register the future
        b.resolve(captured["rid"], "file body", False)
        result, is_error = await task
        self.assertEqual((result, is_error), ("file body", False))
        self.assertTrue(captured["rid"].startswith("w0-"))
        self.assertEqual(captured["gid"], 1)

    async def test_request_not_ready(self):
        b = MCPBridge()                              # no install → bus not ready
        result, is_error = await b.request("x__y", {}, group_id=1, trace_id="t")
        self.assertTrue(is_error)
        self.assertIn("未就绪", result)

    async def test_request_timeout(self):
        b = MCPBridge()
        async def send(*a): pass
        b.install(send, "w0")
        result, is_error = await b.request("x__y", {}, group_id=1, trace_id="t", timeout=0.05)
        self.assertTrue(is_error)
        self.assertIn("超时", result)

    async def test_reset_fails_pending(self):
        b = MCPBridge()
        async def send(*a): pass
        b.install(send, "w0")
        task = asyncio.create_task(b.request("x__y", {}, group_id=1, trace_id="t"))
        await asyncio.sleep(0)
        b.reset()
        result, is_error = await task
        self.assertTrue(is_error)
        self.assertIn("总线已断开", result)


class TestMcpProxyProvider(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._saved = list(global_bridge.schemas)
        global_bridge.set_schemas([
            {"function": {"name": "fs__read_file", "description": "read", "parameters": {}}},
            {"function": {"name": "fs__write_file", "description": "write", "parameters": {}}},
        ])

    def tearDown(self):
        global_bridge.set_schemas(self._saved)

    def test_discover_and_can_handle(self):
        p = McpProxyProvider()
        names = {t.name for t in p.discover_tools()}
        self.assertEqual(names, {"fs__read_file", "fs__write_file"})
        self.assertTrue(p.can_handle("fs__read_file"))
        self.assertFalse(p.can_handle("read_file"))

    async def test_read_tool_no_hil_forwards(self):
        p = McpProxyProvider()
        with patch.object(global_bridge, "request",
                          new=AsyncMock(return_value=("ok", False))) as req:
            result, is_error = await p.execute("fs__read_file", {"path": "/x"}, {})
            self.assertEqual((result, is_error), ("ok", False))
            req.assert_awaited_once()

    async def test_write_tool_without_ruleset_blocked(self):
        p = McpProxyProvider()
        with patch.object(global_bridge, "request", new=AsyncMock()) as req:
            result, is_error = await p.execute("fs__write_file", {"path": "/x", "content": "y"}, {})
            self.assertTrue(is_error)
            self.assertIn("安全拦截", result)
            req.assert_not_awaited()       # blocked before forwarding


if __name__ == "__main__":
    unittest.main()
