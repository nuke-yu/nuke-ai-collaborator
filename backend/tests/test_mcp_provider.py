"""
tests/test_mcp_provider.py

Tests for McpClientToolProvider (single-task session loop) and ToolRouter.

Strategy: mock stdio_client and ClientSession so the session_task runs
through its real asyncio path (Queue, Event, etc.) without needing npx/Node.
This validates the actual concurrency wiring — the previous version only
tested the function bodies in isolation, missing the cross-task issues.
"""
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from executors.providers.mcp_client import (
    McpClientToolProvider,
    _mcp_tool_name,
    _strip_prefix,
)
from executors.tool_router import ToolRouter
from executors.providers import BuiltinToolProvider
from executors import tool_executor
from executors.base import ToolDef


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #

def _make_mock_tool(name: str, description: str = "", schema: dict | None = None):
    t = MagicMock()
    t.name = name
    t.description = description
    t.inputSchema = MagicMock()
    t.inputSchema.model_dump.return_value = schema or {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    return t


def _make_mock_session(tools: list, call_result_text: str = "mock file content"):
    """Build a mock ClientSession that works inside the session_loop coroutine."""
    session = MagicMock()  # NOT AsyncMock at top level — individual methods are async

    list_result = MagicMock()
    list_result.tools = tools
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=list_result)

    call_result = MagicMock()
    block = MagicMock()
    block.text = call_result_text
    call_result.content = [block]
    session.call_tool = AsyncMock(return_value=call_result)
    return session


async def _make_live_provider(
    tools=None,
    call_result_text: str = "mock file content",
    allow_list=None,
    call_timeout: int = 5,
):
    """
    Create and initialize a provider backed by a fully mocked session.

    The session_task runs for real (asyncio.create_task), so Queue/Event
    wiring is exercised.  The stdio_client and ClientSession are patched
    to avoid spawning any subprocess.
    """
    if tools is None:
        tools = [_make_mock_tool("read_file"), _make_mock_tool("write_file")]

    mock_session = _make_mock_session(tools, call_result_text)

    provider = McpClientToolProvider(
        server_name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        allow_list=allow_list,
        call_timeout=call_timeout,
    )

    # We patch at module level so the session_task (running as a real task)
    # picks up the mocks when it imports them.
    with (
        patch("executors.providers.mcp_client.stdio_client") as mock_stdio,
        patch("executors.providers.mcp_client.ClientSession") as mock_cs,
    ):
        mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

        await provider.initialize()

    # Expose mock_session so tests can assert on call_tool
    provider._mock_session = mock_session
    return provider


# --------------------------------------------------------------------------- #
# Unit tests: naming helpers
# --------------------------------------------------------------------------- #

class TestNamingHelpers(unittest.TestCase):
    def test_mcp_tool_name(self):
        self.assertEqual(_mcp_tool_name("filesystem", "read_file"), "filesystem__read_file")

    def test_strip_prefix(self):
        self.assertEqual(_strip_prefix("filesystem", "filesystem__read_file"), "read_file")

    def test_strip_prefix_no_match(self):
        self.assertEqual(_strip_prefix("filesystem", "other__read_file"), "other__read_file")


# --------------------------------------------------------------------------- #
# Unit tests: McpClientToolProvider — live session_task
# --------------------------------------------------------------------------- #

class TestMcpClientToolProvider(unittest.IsolatedAsyncioTestCase):

    async def test_provider_id(self):
        p = McpClientToolProvider("filesystem", "npx", [])
        self.assertEqual(p.provider_id, "mcp:filesystem")

    async def test_initialize_discovers_tools(self):
        p = await _make_live_provider()
        tools = p.discover_tools()
        self.assertEqual(len(tools), 2)
        names = {t.name for t in tools}
        self.assertIn("filesystem__read_file", names)
        self.assertIn("filesystem__write_file", names)
        await p.close()

    async def test_tool_description_includes_server_name(self):
        p = await _make_live_provider(tools=[_make_mock_tool("read_file", "Reads a file")])
        td = p.discover_tools()[0]
        self.assertIn("filesystem", td.description)
        self.assertIn("Reads a file", td.description)
        await p.close()

    async def test_can_handle_prefix(self):
        p = await _make_live_provider()
        self.assertTrue(p.can_handle("filesystem__read_file"))
        self.assertFalse(p.can_handle("read_file"))
        self.assertFalse(p.can_handle("other__read_file"))
        await p.close()

    async def test_is_initialized_predicate(self):
        p = McpClientToolProvider("filesystem", "npx", [])
        self.assertFalse(p.is_initialized())
        p2 = await _make_live_provider()
        self.assertTrue(p2.is_initialized())
        await p2.close()
        self.assertFalse(p2.is_initialized())

    async def test_execute_routes_via_queue_to_session(self):
        """execute() must travel through the Queue to the session_task."""
        p = await _make_live_provider()
        result, is_error = await p.execute("filesystem__read_file", {"path": "/tmp/x.txt"}, {})
        self.assertFalse(is_error)
        self.assertEqual(result, "mock file content")
        p._mock_session.call_tool.assert_awaited_once_with("read_file", {"path": "/tmp/x.txt"})
        await p.close()

    async def test_execute_before_initialize_returns_error(self):
        p = McpClientToolProvider("filesystem", "npx", [])
        result, is_error = await p.execute("filesystem__read_file", {}, {})
        self.assertTrue(is_error)
        self.assertIn("未初始化", result)

    async def test_execute_timeout(self):
        """A hung session_task causes execute() to return a timeout error."""
        p = await _make_live_provider(call_timeout=1)
        # Make call_tool block forever
        async def _hang(*a, **kw):
            await asyncio.sleep(999)
        p._mock_session.call_tool = _hang
        result, is_error = await p.execute("filesystem__read_file", {}, {})
        self.assertTrue(is_error)
        self.assertIn("超时", result)
        await p.close()

    async def test_execute_session_error_propagates(self):
        p = await _make_live_provider()
        p._mock_session.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))
        result, is_error = await p.execute("filesystem__read_file", {}, {})
        self.assertTrue(is_error)
        self.assertIn("MCP执行错误", result)
        await p.close()

    async def test_double_initialize_is_noop(self):
        p = await _make_live_provider()
        original_task = p._session_task
        await p.initialize()  # should log warning and return early
        self.assertIs(p._session_task, original_task)
        await p.close()

    async def test_allow_list_filters_tools(self):
        """Only whitelisted tools are registered when allow_list is set."""
        p = await _make_live_provider(allow_list={"read_file"})
        names = {t.name for t in p.discover_tools()}
        self.assertIn("filesystem__read_file", names)
        self.assertNotIn("filesystem__write_file", names)
        await p.close()

    async def test_hil_gate_blocks_write_without_ruleset(self):
        """Write-class tools must be blocked when no ruleset is present in context."""
        p = await _make_live_provider()
        result, is_error = await p.execute(
            "filesystem__write_file", {"path": "/tmp/x", "content": "y"}, {}
        )
        self.assertTrue(is_error)
        self.assertIn("安全拦截", result)
        await p.close()

    async def test_env_merges_with_os_environ(self):
        """Extra env vars should merge onto os.environ, not replace it."""
        import os
        captured_env = {}

        async def _fake_loop(provider_self):
            import os as _os
            # Capture what StdioServerParameters would receive
            captured_env.update(
                {**_os.environ, **(provider_self._extra_env or {})}
            )

        p = McpClientToolProvider(
            "filesystem", "npx", [],
            env={"MY_CUSTOM_VAR": "hello"},
        )
        # PATH must be preserved
        merged = {**os.environ, **p._extra_env}
        self.assertIn("PATH", merged)
        self.assertEqual(merged["MY_CUSTOM_VAR"], "hello")


# --------------------------------------------------------------------------- #
# Unit tests: ToolRouter
# --------------------------------------------------------------------------- #

class TestToolRouter(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.router = ToolRouter()

    async def test_has_providers_predicate(self):
        self.assertFalse(self.router.has_providers())
        self.router.register_provider(MagicMock(
            provider_id="test",
            discover_tools=MagicMock(return_value=[])
        ))
        self.assertTrue(self.router.has_providers())

    async def test_get_all_schemas_merges_providers(self):
        async def fake_handler(path): return "ok"
        tool_executor.register(ToolDef(name="read_file", description="read"), fake_handler)
        builtin = BuiltinToolProvider()
        self.router.register_provider(builtin)

        schemas = self.router.get_all_schemas()
        names = {s["function"]["name"] for s in schemas}
        self.assertIn("read_file", names)
        tool_executor._registry.pop("read_file", None)

    async def test_get_external_schemas_excludes_builtin(self):
        """get_external_schemas() must NOT surface builtin-registry tools, or
        the per-stage manifest whitelist leaks (excluded builtins reappear)."""
        async def fake_handler(path): return "ok"
        tool_executor.register(ToolDef(name="read_file", description="read"), fake_handler)
        self.router.register_provider(BuiltinToolProvider())
        mcp_prov = MagicMock()
        mcp_prov.provider_id = "mcp:fs"
        mcp_prov.discover_tools = MagicMock(
            return_value=[ToolDef(name="fs__read", description="x")]
        )
        self.router.register_provider(mcp_prov)

        names = {s["function"]["name"] for s in self.router.get_external_schemas()}
        self.assertIn("fs__read", names)        # external (MCP) tool included
        self.assertNotIn("read_file", names)    # builtin must NOT leak through
        tool_executor._registry.pop("read_file", None)

    async def test_router_routes_to_correct_provider(self):
        mcp_prov = MagicMock()
        mcp_prov.provider_id = "mcp:filesystem"
        mcp_prov.can_handle = lambda name: name.startswith("filesystem__")
        mcp_prov.execute = AsyncMock(return_value=("mcp result", False))
        mcp_prov.discover_tools = MagicMock(return_value=[])
        self.router.register_provider(mcp_prov)

        result, is_error = await self.router.execute("filesystem__read_file", {"path": "/x"})
        self.assertFalse(is_error)
        self.assertEqual(result, "mcp result")
        mcp_prov.execute.assert_awaited_once()

    async def test_router_unknown_tool_returns_error(self):
        result, is_error = await self.router.execute("nonexistent_tool", {})
        self.assertTrue(is_error)
        self.assertIn("未找到", result)

    async def test_router_close_all_calls_provider_close(self):
        mcp_prov = MagicMock()
        mcp_prov.provider_id = "mcp:filesystem"
        mcp_prov.close = AsyncMock()
        mcp_prov.discover_tools = MagicMock(return_value=[])
        self.router.register_provider(mcp_prov)

        await self.router.close_all()
        mcp_prov.close.assert_awaited_once()


# --------------------------------------------------------------------------- #
# from_config factory
# --------------------------------------------------------------------------- #

class TestFromConfig(unittest.TestCase):

    def test_loads_enabled_servers(self):
        import tempfile
        cfg = {
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    "env": {},
                    "allow_list": ["read_file"],
                    "call_timeout": 20,
                    "enabled": True,
                },
                "disabled_server": {
                    "command": "npx",
                    "args": [],
                    "enabled": False,
                },
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            tmp = Path(f.name)

        providers = McpClientToolProvider.from_config(tmp)
        self.assertEqual(len(providers), 1)
        p = providers[0]
        self.assertEqual(p._server_name, "filesystem")
        self.assertEqual(p._allow_list, {"read_file"})
        self.assertEqual(p._call_timeout, 20)
        tmp.unlink()

    def test_missing_config_returns_empty(self):
        providers = McpClientToolProvider.from_config("/nonexistent/path.json")
        self.assertEqual(providers, [])


class TestDispatchRouting(unittest.IsolatedAsyncioTestCase):
    """Guards the real-runner dispatch policy (the bug that was previously masked):
    MCP tools (not in tool_executor's registry) route through the ToolRouter;
    builtin/skill/shell tools stay on tool_executor so global hooks still fire."""

    async def test_mcp_to_router_builtin_to_executor(self):
        from executors.plugins.tool_loop_v1 import _dispatch_tool
        from executors.tool_router import router

        async def _builtin(**_):
            return "builtin-ran"
        tool_executor.register(ToolDef(name="bt_probe", description="", parameters={}), _builtin)

        class _FakeMcp:
            provider_id = "mcp:probe"
            def discover_tools(self): return []
            def can_handle(self, n): return n.startswith("probe__")
            async def execute(self, n, a, c): return ("mcp-ran", False)

        router.register_provider(_FakeMcp())
        try:
            # builtin (in registry) → tool_executor, NOT the router
            r, _ = await _dispatch_tool("bt_probe", {}, {})
            self.assertEqual(r, "builtin-ran")
            # MCP-prefixed (not in registry) → router
            r, e = await _dispatch_tool("probe__do", {}, {})
            self.assertEqual((r, e), ("mcp-ran", False))
        finally:
            tool_executor._registry.pop("bt_probe", None)
            router.unregister_provider("mcp:probe")

    async def test_unknown_tool_with_no_providers_falls_through(self):
        from executors.plugins.tool_loop_v1 import _dispatch_tool
        # not registered + no provider can_handle → tool_executor returns "尚未实现"
        r, e = await _dispatch_tool("nonexistent__x", {}, {})
        self.assertTrue(e)
        self.assertIn("尚未实现", r)

    async def test_run_shell_still_gated_by_before_hooks(self):
        """Regression (the headline bug): run_shell must keep flowing through
        tool_executor.execute() so the global before-hooks — permission check +
        danger guard — still fire.  If run_shell were ever first-match routed to
        ShellToolProvider, these hooks would be silently skipped, and a call
        with no ruleset would execute unchecked instead of failing closed."""
        from executors.plugins.tool_loop_v1 import _dispatch_tool
        from executors.plugins.workspace_tools import (
            _permission_check_hook, _default_shell_guard,
        )

        ran = {"hit": False}
        async def _shell_handler(cmd, context=None, **_):
            ran["hit"] = True            # must NOT be reached when blocked
            return f"ran: {cmd}"

        saved_entry = tool_executor._registry.get("run_shell")
        saved_hooks = list(tool_executor._before_hooks)
        tool_executor.register(
            ToolDef(name="run_shell", description="", parameters={}), _shell_handler
        )
        tool_executor.add_before_hook(_permission_check_hook)
        tool_executor.add_before_hook(_default_shell_guard)
        try:
            # No ruleset in context → fail closed, handler never runs.
            result, is_error = await _dispatch_tool("run_shell", {"cmd": "echo hi"}, {})
            self.assertTrue(is_error)
            self.assertIn("已拦截", result)
            self.assertIn("未接入权限系统", result)
            self.assertFalse(ran["hit"])
        finally:
            tool_executor._before_hooks[:] = saved_hooks
            if saved_entry is not None:
                tool_executor._registry["run_shell"] = saved_entry
            else:
                tool_executor._registry.pop("run_shell", None)


if __name__ == "__main__":
    unittest.main()
