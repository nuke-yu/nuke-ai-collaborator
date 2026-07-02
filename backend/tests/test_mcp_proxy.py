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

    def test_schema_for_o1_index(self):
        b = MCPBridge()
        b.set_schemas([{"function": {"name": "a__x", "description": "", "parameters": {}}}])
        self.assertIsNotNone(b.schema_for("a__x"))
        self.assertIsNone(b.schema_for("nope"))
        b.set_schemas([])                       # rebuilt on each set
        self.assertIsNone(b.schema_for("a__x"))

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
        self.assertEqual(b._pending, {})

    def test_resolve_unknown_request_logs_and_does_not_mutate_pending(self):
        b = MCPBridge()

        with self.assertLogs("executors.mcp_bridge", level="WARNING") as logs:
            b.resolve("missing-rid", "late", False)

        self.assertEqual(b._pending, {})
        self.assertTrue(any("unknown request_id=missing-rid" in line for line in logs.output))

    async def test_late_result_after_timeout_logs_without_recreating_pending(self):
        b = MCPBridge()
        captured = {}

        async def send(rid, *_args):
            captured["rid"] = rid

        b.install(send, "w0")
        result, is_error = await b.request("x__y", {}, group_id=1, trace_id="t", timeout=0.05)
        self.assertTrue(is_error)
        self.assertEqual(b._pending, {})

        with self.assertLogs("executors.mcp_bridge", level="WARNING") as logs:
            b.resolve(captured["rid"], "late", False)

        self.assertEqual(b._pending, {})
        self.assertTrue(any(f"unknown request_id={captured['rid']}" in line for line in logs.output))

    async def test_authenticate_roundtrip(self):
        b = MCPBridge()
        captured = {}
        async def send_auth(rid, server, gid, tid):
            captured.update(rid=rid, server=server)
        b.install(AsyncMock(), "w0")
        b.install_auth(send_auth)
        task = asyncio.create_task(b.authenticate("github", group_id=1, trace_id="t"))
        await asyncio.sleep(0)
        b.resolve(captured["rid"], "https://auth/url", False)
        result, is_error = await task
        self.assertEqual((result, is_error), ("https://auth/url", False))
        self.assertEqual(captured["server"], "github")
        self.assertIn("auth", captured["rid"])

    async def test_authenticate_no_bus(self):
        b = MCPBridge()                               # no install_auth
        result, is_error = await b.authenticate("x", group_id=1, trace_id="t")
        self.assertTrue(is_error)
        self.assertIn("未就绪", result)

    async def test_reset_fails_pending(self):
        b = MCPBridge()
        async def send(*a): pass
        b.install(send, "w0")
        b.set_schemas([{"function": {"name": "fs__read_file", "parameters": {}}}])
        task = asyncio.create_task(b.request("x__y", {}, group_id=1, trace_id="t"))
        await asyncio.sleep(0)
        b.reset()
        result, is_error = await task
        self.assertTrue(is_error)
        self.assertIn("总线已断开", result)
        self.assertEqual(b.schemas, [])
        self.assertIsNone(b.schema_for("fs__read_file"))

    async def test_request_cancellation_cleans_pending(self):
        b = MCPBridge()
        blocker = asyncio.Event()

        async def send(*a):
            await blocker.wait()

        b.install(send, "w0")
        task = asyncio.create_task(b.request("x__y", {}, group_id=1, trace_id="t", timeout=1))
        await asyncio.sleep(0)
        self.assertEqual(len(b._pending), 1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(b._pending, {})
        blocker.set()

    async def test_authenticate_timeout_cleans_pending(self):
        b = MCPBridge()

        async def send_auth(*a):
            return None

        b.install(AsyncMock(), "w0")
        b.install_auth(send_auth)
        result, is_error = await b.authenticate("github", group_id=1, trace_id="t", timeout=0.05)
        self.assertTrue(is_error)
        self.assertIn("超时", result)
        self.assertEqual(b._pending, {})


class TestMcpProxyProvider(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._saved = list(global_bridge.schemas)
        # The collector ALWAYS annotates needs_approval; mirror that here so the
        # proxy exercises its realistic path (a flagless schema is fail-closed).
        global_bridge.set_schemas([
            {"function": {"name": "fs__read_file", "description": "read", "parameters": {}},
             "needs_approval": False},
            {"function": {"name": "fs__write_file", "description": "write", "parameters": {}},
             "needs_approval": True},
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

    async def test_per_server_flag_gates_read_named_tool(self):
        # server flagged a read-NAMED tool for approval → gated despite the heuristic
        global_bridge.set_schemas([
            {"function": {"name": "fs__read_file", "description": "", "parameters": {}},
             "needs_approval": True},
        ])
        p = McpProxyProvider()
        with patch.object(global_bridge, "request", new=AsyncMock()) as req:
            result, is_error = await p.execute("fs__read_file", {}, {})   # no ruleset
            self.assertTrue(is_error)
            self.assertIn("安全拦截", result)
            req.assert_not_awaited()

    async def test_unnamespaced_tool_requires_approval(self):
        # fail-safe: a name without '__' can't be classified → must require approval
        global_bridge.set_schemas([{"function": {"name": "weirdname", "parameters": {}}}])
        p = McpProxyProvider()
        with patch.object(global_bridge, "request", new=AsyncMock()) as req:
            result, is_error = await p.execute("weirdname", {}, {})   # no ruleset
            self.assertTrue(is_error)
            self.assertIn("安全拦截", result)
            req.assert_not_awaited()

    async def test_flagless_schema_fails_closed(self):
        # A namespaced tool whose schema lacks needs_approval (collector hasn't
        # shipped the flag yet) must be gated, not guessed from the name.
        global_bridge.set_schemas([{"function": {"name": "fs__read_file", "parameters": {}}}])
        p = McpProxyProvider()
        with patch.object(global_bridge, "request", new=AsyncMock()) as req:
            result, is_error = await p.execute("fs__read_file", {}, {})   # no ruleset
            self.assertTrue(is_error)
            self.assertIn("安全拦截", result)
            req.assert_not_awaited()

    async def test_always_approval_hotpatched_into_ruleset(self):
        # ③ in-session immediacy: an "always" approval appends the persist_rule to
        # the live ruleset so the next identical call in the same loop won't re-ask.
        class _RS:
            def __init__(self): self.rules = []
        rs = _RS()
        rule = object()
        p = McpProxyProvider()
        with (
            patch("permissions.check",
                  new=AsyncMock(return_value={"action": "allow", "persist_rule": rule})),
            patch.object(global_bridge, "request",
                         new=AsyncMock(return_value=("ok", False))) as req,
        ):
            result, is_error = await p.execute("fs__write_file", {"path": "/x"}, {"ruleset": rs})
        self.assertEqual((result, is_error), ("ok", False))
        self.assertIn(rule, rs.rules)         # rule took effect immediately
        req.assert_awaited_once()

    async def test_per_server_flag_allows_write_named_tool(self):
        # server flagged a write-NAMED tool as NOT needing approval → forwarded
        global_bridge.set_schemas([
            {"function": {"name": "fs__write_file", "description": "", "parameters": {}},
             "needs_approval": False},
        ])
        p = McpProxyProvider()
        with patch.object(global_bridge, "request", new=AsyncMock(return_value=("ok", False))) as req:
            result, is_error = await p.execute("fs__write_file", {"path": "/x"}, {})
            self.assertEqual((result, is_error), ("ok", False))
            req.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
