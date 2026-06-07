"""
providers/mcp_client.py — McpClientToolProvider

Connects to a single MCP server over stdio using the official `mcp` Python SDK.
Lifecycle:
  1. call await provider.initialize()   — launches subprocess, handshakes, caches tool list
  2. provider is registered with ToolRouter
  3. ToolRouter routes tool calls to provider.execute()
  4. on shutdown, call await provider.close()

Tool naming:
  MCP tools are exposed with a "{server_name}__{tool_name}" prefix so they
  don't clash with builtin tools.  Example: "filesystem__read_file".
  can_handle() checks for this prefix; execute() strips it before forwarding
  to the actual MCP server.

Error handling:
  - If the subprocess dies mid-session, execute() returns (error_msg, True).
  - Re-initialize is the caller's responsibility (ToolRouter.reinitialize_mcp()).
"""
import json
import logging
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from executors.base import ToolDef, ToolProvider

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Canonical exposed name: 'filesystem__read_file'."""
    return f"{server_name}__{tool_name}"


def _strip_prefix(server_name: str, exposed_name: str) -> str:
    """'filesystem__read_file' → 'read_file'."""
    prefix = f"{server_name}__"
    return exposed_name[len(prefix):] if exposed_name.startswith(prefix) else exposed_name


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #

class McpClientToolProvider(ToolProvider):
    """
    ToolProvider backed by a single MCP server process (stdio transport).

    Parameters
    ----------
    server_name : str
        Logical name used for tool prefixing and logging (e.g. "filesystem").
    command : str
        Executable to launch (e.g. "npx").
    args : list[str]
        Arguments for the command.
    env : dict[str, str] | None
        Extra environment variables merged onto the current env.
    """

    def __init__(
        self,
        server_name: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ):
        self._server_name = server_name
        self._command = command
        self._args = args
        self._env = env or {}

        self._session = None          # mcp.ClientSession, set after initialize()
        self._exit_stack: AsyncExitStack | None = None
        self._tools: list[ToolDef] = []   # cached after list_tools()

    # ------------------------------------------------------------------ #
    # ToolProvider interface
    # ------------------------------------------------------------------ #

    @property
    def provider_id(self) -> str:
        return f"mcp:{self._server_name}"

    def discover_tools(self) -> list[ToolDef]:
        """Return cached ToolDefs (populated by initialize())."""
        return list(self._tools)

    def can_handle(self, name: str) -> bool:
        prefix = f"{self._server_name}__"
        return name.startswith(prefix)

    async def execute(self, name: str, arguments: dict, context: dict) -> tuple[str, bool]:
        if self._session is None:
            return f"[MCP错误] 服务器 '{self._server_name}' 尚未初始化", True

        real_name = _strip_prefix(self._server_name, name)
        try:
            result = await self._session.call_tool(real_name, arguments)
            # result.content is a list of ToolResultContent blocks
            texts = []
            for block in result.content:
                if hasattr(block, "text"):
                    texts.append(block.text)
                else:
                    texts.append(str(block))
            return "\n".join(texts) or "完成", False
        except Exception as e:
            logger.error(f"MCP execute error [{self._server_name}/{real_name}]: {e}")
            return f"[MCP执行错误] {e}", True

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def initialize(self) -> None:
        """
        Launch the MCP server subprocess, perform the JSON-RPC handshake,
        and cache the tool list.  Safe to call once per provider lifetime.
        """
        if self._session is not None:
            logger.warning(f"McpClientToolProvider '{self._server_name}' already initialized; skipping.")
            return

        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=self._env or None,
        )

        self._exit_stack = AsyncExitStack()
        try:
            read, write = await self._exit_stack.enter_async_context(stdio_client(params))
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()

            tools_result = await self._session.list_tools()
            self._tools = []
            for t in tools_result.tools:
                exposed_name = _mcp_tool_name(self._server_name, t.name)
                # Convert MCP tool schema → ToolDef
                params_schema = {}
                if t.inputSchema:
                    params_schema = (
                        t.inputSchema.model_dump()
                        if hasattr(t.inputSchema, "model_dump")
                        else dict(t.inputSchema)
                    )
                self._tools.append(ToolDef(
                    name=exposed_name,
                    description=f"[{self._server_name}] {t.description or t.name}",
                    parameters=params_schema,
                ))

            logger.info(
                f"MCP provider '{self._server_name}' initialized: "
                f"{len(self._tools)} tools — {[d.name for d in self._tools]}"
            )
        except Exception as e:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            raise RuntimeError(f"Failed to initialize MCP server '{self._server_name}': {e}") from e

    async def close(self) -> None:
        """Terminate the MCP server subprocess and release all resources."""
        if self._exit_stack:
            await self._exit_stack.aclose()
        self._session = None
        self._exit_stack = None
        self._tools = []
        logger.info(f"MCP provider '{self._server_name}' closed.")

    # ------------------------------------------------------------------ #
    # Factory: load from mcp_servers.json
    # ------------------------------------------------------------------ #

    @classmethod
    def from_config(cls, config_path: str | Path) -> list["McpClientToolProvider"]:
        """
        Parse a mcp_servers.json and return one provider per enabled server.

        Expected format (compatible with Claude Desktop):
        {
          "mcpServers": {
            "filesystem": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"],
              "env": {},
              "enabled": true
            }
          }
        }
        """
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"mcp_servers.json not found at {path}; no MCP providers loaded.")
            return []

        with path.open() as f:
            cfg = json.load(f)

        providers = []
        for name, spec in cfg.get("mcpServers", {}).items():
            if not spec.get("enabled", True):
                logger.info(f"MCP server '{name}' is disabled; skipping.")
                continue
            providers.append(cls(
                server_name=name,
                command=spec["command"],
                args=spec.get("args", []),
                env=spec.get("env", {}),
            ))
        return providers
