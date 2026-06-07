"""
tests/test_mcp_provider.py

Tests for McpClientToolProvider and ToolRouter.

Strategy: mock the mcp.ClientSession so no real subprocess is needed.
This lets the tests run in CI without npx / Node installed.

Real end-to-end smoke test (requires npx):
    pytest tests/test_mcp_provider.py -m e2e -s
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from executors.providers.mcp_client import McpClientToolProvider, _mcp_tool_name, _strip_prefix
from executors.tool_router import ToolRouter
from executors.providers import BuiltinToolProvider
from executors import tool_executor
from executors.base import ToolDef


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


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


def _make_mock_session(tools: list):
    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = tools
    session.list_tools = AsyncMock(return_value=list_result)
    session.initialize = AsyncMock()

    call_result = MagicMock()
    block = MagicMock()
    block.text = "mock file content"
    call_result.content = [block]
    session.call_tool = AsyncMock(return_value=call_result)
    return session


# --------------------------------------------------------------------------- #
# Unit tests: naming helpers
# --------------------------------------------------------------------------- #

class TestNamingHelpers(unittest.TestCase):
    def test_mcp_tool_name(self):
        self.assertEqual(_mcp_tool_name("filesystem", "read_file"), "filesystem__read_file")

    def test_strip_prefix(self):
        self.assertEqual(_strip_prefix("filesystem", "filesystem__read_file"), "read_file")

    def test_strip_prefix_no_match(self):
        # If name doesn't start with prefix, return as-is (defensive)
        self.assertEqual(_strip_prefix("filesystem", "other__read_file"), "other__read_file")


# --------------------------------------------------------------------------- #
# Unit tests: McpClientToolProvider (mocked session)
# --------------------------------------------------------------------------- #

class TestMcpClientToolProvider(unittest.IsolatedAsyncioTestCase):

    async def _make_initialized_provider(self, tools=None):
        if tools is None:
            tools = [_make_mock_tool("read_file"), _make_mock_tool("write_file")]

        provider = McpClientToolProvider(
            server_name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        mock_session = _make_mock_session(tools)

        with (
            patch("executors.providers.mcp_client.stdio_client") as mock_stdio,
            patch("executors.providers.mcp_client.ClientSession") as mock_cs,
        ):
            # stdio_client is an async context manager yielding (read, write)
            mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
            mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)
            # ClientSession is an async context manager yielding the session
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            await provider.initialize()

        # Patch the session directly for subsequent calls
        provider._session = mock_session
        return provider

    async def test_provider_id(self):
        p = McpClientToolProvider("filesystem", "npx", [])
        self.assertEqual(p.provider_id, "mcp:filesystem")

    async def test_initialize_discovers_tools(self):
        p = await self._make_initialized_provider()
        tools = p.discover_tools()
        self.assertEqual(len(tools), 2)
        names = {t.name for t in tools}
        self.assertIn("filesystem__read_file", names)
        self.assertIn("filesystem__write_file", names)

    async def test_tool_description_includes_server_name(self):
        p = await self._make_initialized_provider(
            tools=[_make_mock_tool("read_file", "Reads a file")]
        )
        td = p.discover_tools()[0]
        self.assertIn("filesystem", td.description)
        self.assertIn("Reads a file", td.description)

    async def test_can_handle_prefix(self):
        p = await self._make_initialized_provider()
        self.assertTrue(p.can_handle("filesystem__read_file"))
        self.assertFalse(p.can_handle("read_file"))         # bare name: not ours
        self.assertFalse(p.can_handle("other__read_file"))  # wrong server

    async def test_execute_strips_prefix_and_calls_session(self):
        p = await self._make_initialized_provider()
        result, is_error = await p.execute("filesystem__read_file", {"path": "/tmp/x.txt"}, {})
        self.assertFalse(is_error)
        self.assertEqual(result, "mock file content")
        p._session.call_tool.assert_awaited_once_with("read_file", {"path": "/tmp/x.txt"})

    async def test_execute_before_initialize_returns_error(self):
        p = McpClientToolProvider("filesystem", "npx", [])
        result, is_error = await p.execute("filesystem__read_file", {}, {})
        self.assertTrue(is_error)
        self.assertIn("尚未初始化", result)

    async def test_execute_propagates_session_error(self):
        p = await self._make_initialized_provider()
        p._session.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))
        result, is_error = await p.execute("filesystem__read_file", {}, {})
        self.assertTrue(is_error)
        self.assertIn("MCP执行错误", result)

    async def test_double_initialize_is_noop(self):
        p = await self._make_initialized_provider()
        original_session = p._session
        # Second initialize should return early without replacing the session
        await p.initialize()
        self.assertIs(p._session, original_session)


# --------------------------------------------------------------------------- #
# Unit tests: ToolRouter
# --------------------------------------------------------------------------- #

class TestToolRouter(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.router = ToolRouter()

    async def test_get_all_schemas_merges_providers(self):
        # Builtin provider with one tool
        async def fake_handler(path): return "ok"
        tool_executor.register(ToolDef(name="read_file", description="read"), fake_handler)
        builtin = BuiltinToolProvider()
        self.router.register_provider(builtin)

        schemas = self.router.get_all_schemas()
        names = {s["function"]["name"] for s in schemas}
        self.assertIn("read_file", names)

        tool_executor._registry.pop("read_file", None)

    async def test_router_routes_to_correct_provider(self):
        # MCP provider mock
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

    def test_loads_enabled_servers(self, tmp_path=None):
        import tempfile, json, pathlib
        cfg = {
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    "env": {},
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
            tmp = pathlib.Path(f.name)

        providers = McpClientToolProvider.from_config(tmp)
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]._server_name, "filesystem")
        tmp.unlink()

    def test_missing_config_returns_empty(self):
        providers = McpClientToolProvider.from_config("/nonexistent/path.json")
        self.assertEqual(providers, [])


if __name__ == "__main__":
    unittest.main()
