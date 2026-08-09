"""Tests for the MCP collector process (cross-group MCP owner on the bus).

Drives a real MCPCollector against a minimal in-test supervisor over the real
ipc transport: validates HELLO registration, schema push, and the
MCP_CALL → execute(pre_authorized) → MCP_RESULT round-trip with correlation.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

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

    async def test_auth_lock_is_cleaned_when_handler_is_cancelled(self):
        coll = MCPCollector("dummy")
        provider = MagicMock(url="https://mcp.example", oauth_cfg={})
        coll._find_provider = MagicMock(return_value=provider)
        coll._build_auth_provider = AsyncMock(return_value=object())
        coll._flows.begin = MagicMock(return_value=asyncio.Future())
        coll._writer = object()
        with patch("runtime.mcp_collector.ipc.send_msg", new=AsyncMock()):
            task = asyncio.create_task(coll._handle_auth_start({
                "request_id": "cancelled", "origin_worker_id": "w0", "server": "gh", "group_id": 1,
            }))
            for _ in range(100):
                if "gh" in coll._auth_locks:
                    break
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertNotIn("gh", coll._auth_locks)

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

    async def test_mcp_reload_is_serialized(self):
        coll = MCPCollector("dummy")
        coll._router = MagicMock()
        coll._router.close_all = AsyncMock()
        coll._push_schemas = AsyncMock()

        call_order = []
        async def fake_init():
            call_order.append("start")
            await asyncio.sleep(0.1)
            call_order.append("end")

        coll._init_providers = fake_init

        t1 = asyncio.create_task(coll._handle_reload())
        t2 = asyncio.create_task(coll._handle_reload())
        
        await asyncio.gather(t1, t2)
        
        self.assertEqual(call_order, ["start", "end", "start", "end"])

    async def test_mcp_reload_continues_after_provider_cleanup_failure(self):
        coll = MCPCollector("dummy")
        coll._router = MagicMock()
        coll._router.close_all = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        coll._init_providers = AsyncMock()
        coll._push_schemas = AsyncMock()

        with self.assertLogs("runtime.mcp_collector", level="ERROR") as logs:
            await coll._handle_reload()

        coll._router.close_all.assert_awaited_once()
        coll._init_providers.assert_awaited_once()
        coll._push_schemas.assert_awaited_once()
        self.assertTrue(any("reload provider cleanup failed" in line for line in logs.output))


class TestCollectorCallTimeout(unittest.IsolatedAsyncioTestCase):
    """A hung MCP server must not hold a collector slot forever: _handle_call
    bounds each execution with MCP_CALL_TIMEOUT_SECONDS and returns an error."""

    async def test_slow_call_times_out(self):
        class _SlowProvider:
            provider_id = "mcp:slow"
            def discover_tools(self): return []
            def can_handle(self, n): return True
            async def execute(self, n, a, c):
                await asyncio.sleep(5)          # longer than the patched timeout
                return ("never", False)

        coll = MCPCollector("x")
        coll._router = ToolRouter()
        coll._router.register_provider(_SlowProvider())
        coll._writer = object()

        sent = []
        async def fake_send(w, m): sent.append(m)
        from core import config
        with patch("runtime.ipc.send_msg", new=fake_send), \
             patch.object(config, "MCP_CALL_TIMEOUT_SECONDS", 0.05):
            await coll._handle_call({
                "request_id": "r1", "origin_worker_id": "w0",
                "tool": "slow__do", "arguments": {}, "group_id": 1, "trace_id": "t1",
            })
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["is_error"])
        self.assertIn("超时", sent[0]["result"])
        self.assertEqual(sent[0]["request_id"], "r1")


class TestCollectorClose(unittest.IsolatedAsyncioTestCase):

    async def test_close_cancels_tasks_and_waits_for_writer(self):
        coll = MCPCollector("x")
        cancelled = []

        async def _hold(label):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(label)
                raise

        coll._tasks = {
            asyncio.create_task(_hold("call")),
            asyncio.create_task(_hold("auth")),
        }
        await asyncio.sleep(0)
        coll._auth_inflight.add("gh")
        coll._auth_locks["gh"] = asyncio.Lock()

        class _Writer:
            def __init__(self):
                self.closed = False
                self.waited = False
            def close(self):
                self.closed = True
            async def wait_closed(self):
                self.waited = True

        writer = _Writer()
        coll._writer = writer
        coll._router = MagicMock()
        coll._router.close_all = AsyncMock()

        with patch("runtime.mcp_collector._kill_descendants", return_value=0), \
             patch("executors.providers.mcp_oauth_store.aclose_all", new=AsyncMock()):
            await coll.close()

        self.assertCountEqual(cancelled, ["call", "auth"])
        coll._router.close_all.assert_awaited_once()
        self.assertTrue(writer.closed)
        self.assertTrue(writer.waited)
        self.assertEqual(coll._tasks, set())
        self.assertEqual(coll._auth_inflight, set())
        self.assertEqual(coll._auth_locks, {})
        self.assertIsNone(coll._writer)

    async def test_close_logs_cleanup_failures_and_continues(self):
        coll = MCPCollector("x")

        class _Writer:
            def __init__(self):
                self.waited = False

            def close(self):
                raise RuntimeError("writer close failed")

            async def wait_closed(self):
                self.waited = True
                raise RuntimeError("writer wait failed")

        writer = _Writer()
        coll._writer = writer
        coll._router = MagicMock()
        coll._router.close_all = AsyncMock(side_effect=RuntimeError("router close failed"))

        with patch("runtime.mcp_collector._kill_descendants", return_value=0) as kill_descendants, \
             patch("executors.providers.mcp_oauth_store.aclose_all",
                   new=AsyncMock(side_effect=RuntimeError("oauth close failed"))), \
             self.assertLogs("runtime.mcp_collector", level="ERROR") as logs:
            await coll.close()

        self.assertTrue(writer.waited)
        self.assertIsNone(coll._writer)
        kill_descendants.assert_called_once()
        self.assertTrue(any("router close_all failed during shutdown" in line for line in logs.output))
        self.assertTrue(any("writer close failed during shutdown" in line for line in logs.output))
        self.assertTrue(any("writer wait_closed failed during shutdown" in line for line in logs.output))
        self.assertTrue(any("OAuth token-store close failed during shutdown" in line for line in logs.output))


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

    def test_schema_approval_flag_failure_is_logged_and_schema_remains(self):
        from runtime.mcp_collector import MCPCollector

        class _P:
            provider_id = "mcp:bad"
            def discover_tools(self):
                return [ToolDef(name="bad__do", description="", parameters={})]
            def can_handle(self, n): return n.startswith("bad__")
            async def execute(self, n, a, c): return ("", False)
            def approval_flags(self):
                raise RuntimeError("flags failed")

        coll = MCPCollector("x")
        coll._router = ToolRouter()
        coll._router.register_provider(_P())

        with self.assertLogs("runtime.mcp_collector", level="ERROR") as logs:
            schemas = coll._schemas()

        self.assertEqual(schemas[0]["function"]["name"], "bad__do")
        self.assertTrue(any("failed to read approval flags from provider mcp:bad" in line for line in logs.output))


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

    async def _run_init_with_fake_oauth_provider(self, token):
        from unittest.mock import AsyncMock
        from runtime.mcp_collector import MCPCollector

        class _P:
            provider_id = "mcp:remote"
            oauth_cfg = {"scope": "x"}
            url = "https://r"
            def __init__(self): self.inited = False
            def set_auth(self, a): pass
            async def initialize(self): self.inited = True
            def discover_tools(self): return []
            def can_handle(self, n): return False

        p = _P()
        coll = MCPCollector("x")
        with patch("executors.providers.mcp_client.McpClientToolProvider.from_config",
                   return_value=[p]), \
             patch.object(MCPCollector, "_build_auth_provider", return_value=object()), \
             patch("executors.providers.mcp_oauth_store.MCPTokenStorage") as MS:
            MS.return_value.get_tokens = AsyncMock(return_value=token)
            await coll._init_providers()
        return p, coll

    async def test_oauth_server_without_token_deferred(self):
        p, coll = await self._run_init_with_fake_oauth_provider(token=None)
        self.assertFalse(p.inited)                     # not connected at startup
        self.assertIn(p, coll._router._providers)      # but registered (mcp_authenticate can retry)

    async def test_oauth_server_with_token_connects(self):
        p, coll = await self._run_init_with_fake_oauth_provider(token=object())
        self.assertTrue(p.inited)                      # stored token → auto-connect

    async def test_auth_start_inflight_guarded(self):
        from runtime.mcp_collector import MCPCollector
        coll = MCPCollector("x")
        coll._writer = object()

        class _P:
            url = "https://r"
            provider_id = "mcp:gh"
        coll._find_provider = lambda s: _P()
        coll._auth_inflight.add("gh")            # a flow is already in progress

        sent = []
        async def fake_send(w, m): sent.append(m)
        with patch("runtime.ipc.send_msg", new=fake_send):
            await coll._handle_auth_start({
                "request_id": "a", "origin_worker_id": "w0", "server": "gh", "group_id": 1})
        self.assertTrue(sent[0]["is_error"])
        self.assertIn("进行中", sent[0]["result"])

    async def test_auth_start_same_server_is_serialized_and_lock_is_released(self):
        from runtime.mcp_collector import MCPCollector
        coll = MCPCollector("x")
        coll._writer = object()

        class _P:
            url = "https://r"
            provider_id = "mcp:gh"
            def set_auth(self, auth): self.auth = auth
            def is_initialized(self): return False
            async def initialize(self): return None

        prov = _P()
        coll._find_provider = lambda s: prov

        start_reinit = asyncio.Event()
        finish_reinit = asyncio.Event()

        async def fake_reinit(_prov, server):
            start_reinit.set()
            await finish_reinit.wait()
            coll._auth_inflight.discard(server)
            coll._cleanup_auth_lock(server)

        first_url = asyncio.Event()
        def fake_begin(server):
            fut = asyncio.get_running_loop().create_future()
            if not first_url.is_set():
                first_url.set()
                async def _resolve():
                    await start_reinit.wait()
                    fut.set_result("https://auth/first")
                asyncio.create_task(_resolve())
            return fut

        sent = []
        async def fake_send(_w, msg):
            sent.append(msg)

        with patch.object(coll, "_build_auth_provider", new=AsyncMock(return_value=object())), \
             patch.object(coll, "_reinit_with_auth", new=fake_reinit), \
             patch.object(coll._flows, "begin", new=fake_begin), \
             patch("runtime.ipc.send_msg", new=fake_send):
            first = asyncio.create_task(coll._handle_auth_start({
                "request_id": "a1", "origin_worker_id": "w0", "server": "gh", "group_id": 1,
            }))
            await start_reinit.wait()
            second = asyncio.create_task(coll._handle_auth_start({
                "request_id": "a2", "origin_worker_id": "w0", "server": "gh", "group_id": 1,
            }))
            for _ in range(100):
                if len(sent) == 2:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(sent), 2)
            finish_reinit.set()
            await asyncio.gather(first, second)
            for _ in range(100):
                if "gh" not in coll._auth_inflight:
                    break
                await asyncio.sleep(0.01)

        self.assertFalse(sent[0]["is_error"])
        self.assertTrue(sent[1]["is_error"])
        self.assertIn("进行中", sent[1]["result"])
        self.assertNotIn("gh", coll._auth_inflight)
        self.assertNotIn("gh", coll._auth_locks)

    async def test_push_schemas_content_aware(self):
        from runtime.mcp_collector import MCPCollector
        coll = MCPCollector("x")
        coll._writer = object()
        seq = iter([
            [{"function": {"name": "t", "description": "v1", "parameters": {}}}],
            [{"function": {"name": "t", "description": "v1", "parameters": {}}}],  # same → skip
            [{"function": {"name": "t", "description": "v2", "parameters": {}}}],  # changed → push
        ])
        coll._schemas = lambda: next(seq)
        sent = []
        async def fake_send(w, m): sent.append(m)
        with patch("runtime.ipc.send_msg", new=fake_send):
            await coll._push_schemas()
            await coll._push_schemas()
            await coll._push_schemas()
        self.assertEqual(len(sent), 2)               # content change (same name) re-pushed

    async def test_callback_url_uses_env(self):
        import os
        from runtime.mcp_collector import MCPCollector
        coll = MCPCollector("x")
        self.assertEqual(coll._callback_url(), "http://127.0.0.1:8000/mcp/oauth/callback")
        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://demo.example.com/"}):
            self.assertEqual(coll._callback_url(), "https://demo.example.com/mcp/oauth/callback")


class TestCollectorOAuthClientSeed(unittest.IsolatedAsyncioTestCase):
    """#1: a configured client_id/secret is pre-seeded into the token store so the
    SDK skips RFC 7591 dynamic registration (servers like GitHub require it)."""

    class _Prov:
        url = "https://r.example.com"
        oauth_cfg = {"client_id": "CID", "client_secret": "CS", "scope": "s"}

    async def _build(self, oauth_cfg, existing=None):
        from runtime.mcp_collector import MCPCollector
        seeded = {}

        class _FakeStore:
            def __init__(self, server): pass
            async def get_client_info(self): return existing
            async def set_client_info(self, ci): seeded["ci"] = ci

        prov = self._Prov(); prov.oauth_cfg = oauth_cfg
        coll = MCPCollector("x")
        with patch("executors.providers.mcp_oauth_store.MCPTokenStorage", _FakeStore), \
             patch("mcp.client.auth.OAuthClientProvider", lambda **kw: ("AUTH", kw)):
            await coll._build_auth_provider(prov, "remote")
        return seeded

    async def test_seeds_when_client_id_present(self):
        seeded = await self._build({"client_id": "CID", "client_secret": "CS", "scope": "s"})
        self.assertEqual(seeded["ci"].client_id, "CID")
        self.assertEqual(seeded["ci"].client_secret, "CS")

    async def test_no_seed_without_client_id(self):
        seeded = await self._build({"scope": "s"})       # dynamic registration path
        self.assertNotIn("ci", seeded)

    async def test_no_reseed_when_already_stored(self):
        from mcp.shared.auth import OAuthClientInformationFull
        existing = OAuthClientInformationFull(client_id="OLD", redirect_uris=["https://x/cb"])
        seeded = await self._build({"client_id": "NEW"}, existing=existing)
        self.assertNotIn("ci", seeded)                   # stored info wins


class TestKillDescendants(unittest.TestCase):
    """#2: process-tree sweep on shutdown (orphaned npx/node grandchildren)."""

    def test_terminates_then_kills_survivors(self):
        from runtime import mcp_collector as mc
        c1, c2 = MagicMock(), MagicMock()
        with patch("psutil.Process") as P, \
             patch("psutil.wait_procs", return_value=([c1], [c2])):
            P.return_value.children.return_value = [c1, c2]
            n = mc._kill_descendants()
        self.assertEqual(n, 2)
        c1.terminate.assert_called_once()
        c2.terminate.assert_called_once()
        c2.kill.assert_called_once()                      # survivor force-killed
        c1.kill.assert_not_called()

    def test_logs_descendant_terminate_and_kill_failures(self):
        from runtime import mcp_collector as mc
        c1, c2 = MagicMock(), MagicMock()
        c1.pid = 111
        c2.pid = 222
        c1.terminate.side_effect = RuntimeError("terminate failed")
        c2.kill.side_effect = RuntimeError("kill failed")

        with patch("psutil.Process") as P, \
             patch("psutil.wait_procs", return_value=([], [c2])), \
             self.assertLogs("runtime.mcp_collector", level="ERROR") as logs:
            P.return_value.children.return_value = [c1, c2]
            n = mc._kill_descendants()

        self.assertEqual(n, 2)
        c1.terminate.assert_called_once()
        c2.terminate.assert_called_once()
        c2.kill.assert_called_once()
        self.assertTrue(any("failed to terminate descendant process 111" in line for line in logs.output))
        self.assertTrue(any("failed to kill descendant process 222" in line for line in logs.output))

    def test_no_children_noop(self):
        from runtime import mcp_collector as mc
        with patch("psutil.Process") as P, patch("psutil.wait_procs", return_value=([], [])):
            P.return_value.children.return_value = []
            self.assertEqual(mc._kill_descendants(), 0)


if __name__ == "__main__":
    unittest.main()
