"""
providers/mcp_client.py — McpClientToolProvider

Connects to a single MCP server over stdio using the official `mcp` Python SDK.

Lifecycle (single-task session ownership):
  1. await provider.initialize()
       → spawns a dedicated asyncio Task (_session_task) that:
           a. opens the stdio_client + ClientSession context managers
           b. calls session.initialize() + list_tools()
           c. enters a request loop, reading from _request_queue
  2. provider is registered with ToolRouter
  3. execute() enqueues a request onto _request_queue and awaits the reply Future
  4. await provider.close() → sends sentinel to the queue → _session_task exits
       cleanly, unwinding both context managers in the same task that entered them.

Why single-task:
  anyio (used by the mcp SDK) binds cancel scopes to the task that created them.
  Entering a context manager in task A and exiting in task B raises:
    RuntimeError: Attempted to exit cancel scope in a different task
  The single-task pattern eliminates this entirely — all enters and exits happen
  within _session_task; callers in other tasks communicate only via Queue/Future.

Tool naming:
  "{server_name}__{tool_name}" e.g. "filesystem__read_file"
  can_handle() checks this prefix; execute() strips it before forwarding.

Security controls (per MCP threat model):
  - call_tool is wrapped with asyncio.wait_for(timeout=self._call_timeout)
  - allow_list: if set, only whitelisted tool names are registered (server-side
    name, before prefixing). Tools not in the list are silently excluded.
  - HIL gate: write/delete-class tools require human approval via the
    permissions system before execution (mirrors the behavior of run_shell).
"""
import asyncio
import json
import logging
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from executors.base import ToolDef, ToolProvider

logger = logging.getLogger(__name__)

# Default per-call timeout for MCP tool invocations (seconds).
_DEFAULT_CALL_TIMEOUT = 30

# MCP tool names that mutate state and therefore require HIL approval
# (mirrors _APPROVAL_REQUIRED_TOOLS in workspace_tools.py).
_MCP_WRITE_TOOLS = frozenset({
    "write_file", "create_file", "delete_file", "move_file", "rename_file",
    "edit_file", "patch_file", "create_directory", "delete_directory",
    "write", "delete", "remove", "move", "rename",
})

# Sentinel object to signal _session_task to exit
_STOP = object()


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
        Logical name for tool prefixing and logging (e.g. "filesystem").
    command : str
        Executable to launch (e.g. "npx").
    args : list[str]
        Arguments for the command.
    env : dict[str, str] | None
        Extra env vars **merged onto** os.environ for the subprocess.
        Only the provided keys override; the rest of the host env (including
        PATH) is preserved so that npx/node can be found.
    allow_list : set[str] | None
        If set, only these server-side tool names are registered.
        Limits attack surface per the MCP supply-chain threat model.
    call_timeout : int
        Per-call timeout in seconds (default 30).  Prevents a hung server
        from blocking the worker indefinitely.
    """

    def __init__(
        self,
        server_name: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        allow_list: set[str] | None = None,
        call_timeout: int = _DEFAULT_CALL_TIMEOUT,
        require_approval_all: bool = False,
        approval_tools: set[str] | None = None,
    ):
        self._server_name = server_name
        self._command = command
        self._args = args
        self._extra_env = env or {}
        self._allow_list = allow_list          # None = accept all
        self._call_timeout = call_timeout
        # HIL policy: which tools require human approval before execution.
        #   require_approval_all=True  → every tool gated (untrusted server)
        #   approval_tools set         → only those server-side names gated
        #   neither                    → fall back to the write-class name heuristic
        self._require_approval_all = require_approval_all
        self._approval_tools = approval_tools

        self._tools: list[ToolDef] = []
        self._request_queue: asyncio.Queue | None = None
        self._session_task: asyncio.Task | None = None
        self._ready = asyncio.Event()          # set when session is initialized
        self._closed = False

    # ------------------------------------------------------------------ #
    # ToolProvider interface
    # ------------------------------------------------------------------ #

    @property
    def provider_id(self) -> str:
        return f"mcp:{self._server_name}"

    def discover_tools(self) -> list[ToolDef]:
        """Return cached ToolDefs (populated after initialize() completes)."""
        return list(self._tools)

    def can_handle(self, name: str) -> bool:
        return name.startswith(f"{self._server_name}__")

    def is_initialized(self) -> bool:
        """Public predicate: True when the session task is alive and ready."""
        return self._session_task is not None and not self._session_task.done()

    def _needs_approval(self, real_name: str) -> bool:
        """Config-driven HIL decision (don't trust the server's tool naming).

        Priority: require_approval_all > explicit approval_tools list > the
        write-class name heuristic (last-resort default for unconfigured servers).
        """
        if self._require_approval_all:
            return True
        if self._approval_tools is not None:
            return real_name in self._approval_tools
        return real_name in _MCP_WRITE_TOOLS

    async def execute(self, name: str, arguments: dict, context: dict) -> tuple[str, bool]:
        if not self.is_initialized():
            return f"[MCP错误] 服务器 '{self._server_name}' 未初始化或已断开", True

        real_name = _strip_prefix(self._server_name, name)

        # HIL gate: write/sensitive tools require human approval (config-driven)
        if self._needs_approval(real_name):
            verdict = await self._check_hil(real_name, arguments, context)
            if verdict:
                return verdict, True

        # Dispatch to the session task via queue
        loop = asyncio.get_event_loop()
        reply_future: asyncio.Future = loop.create_future()
        await self._request_queue.put((real_name, arguments, reply_future))

        try:
            result_text, is_error = await asyncio.wait_for(
                asyncio.shield(reply_future), timeout=self._call_timeout
            )
            return result_text, is_error
        except asyncio.TimeoutError:
            return f"[MCP超时] 工具 '{name}' 执行超过 {self._call_timeout} 秒", True

    # ------------------------------------------------------------------ #
    # HIL gate helper
    # ------------------------------------------------------------------ #

    async def _check_hil(self, tool_name: str, arguments: dict, context: dict) -> str | None:
        """
        Return an error string if the call should be blocked; None to allow.

        Delegates to the permissions system (same as run_shell / write_file).
        If no ruleset is present in context, fail closed for write-class tools.
        """
        import permissions
        ruleset = context.get("ruleset")
        if ruleset is None:
            return (
                f"[MCP安全拦截] '{self._server_name}/{tool_name}' 是写操作类工具，"
                f"但当前会话没有 ruleset，出于安全已拒绝执行"
            )
        result = await permissions.check(
            tool_name=f"mcp::{self._server_name}::{tool_name}",
            arguments=arguments,
            ruleset=ruleset,
            bot_id=context.get("bot_id"),
            broadcaster=context.get("broadcaster"),
            group_id=context.get("group_id"),
            spawn_depth=context.get("spawn_depth", 0),
        )
        if result["action"] == "deny":
            return f"[MCP权限拒绝] {result.get('reason', '权限拒绝')}"
        return None

    # ------------------------------------------------------------------ #
    # Lifecycle — single-task session ownership
    # ------------------------------------------------------------------ #

    async def initialize(self) -> None:
        """
        Spawn the dedicated session task.  Returns once the MCP handshake
        completes and tools are cached (i.e. the provider is ready to serve).
        """
        if self.is_initialized():
            logger.warning(f"McpClientToolProvider '{self._server_name}' already initialized; skipping.")
            return

        self._closed = False
        self._ready.clear()
        self._request_queue = asyncio.Queue()
        self._session_task = asyncio.create_task(
            self._session_loop(), name=f"mcp-session-{self._server_name}"
        )
        # Wait until the session is ready (or the task dies with an error)
        ready_wait = asyncio.create_task(self._ready.wait())
        try:
            done, _ = await asyncio.wait(
                [self._session_task, ready_wait],
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not ready_wait.done():
                ready_wait.cancel()  # don't leak a pending task if the session won the race
        if self._session_task in done:
            # Session task exited before becoming ready → init failed
            exc = self._session_task.exception()
            raise RuntimeError(
                f"Failed to initialize MCP server '{self._server_name}': {exc}"
            ) from exc

    async def _session_loop(self) -> None:
        """
        Long-lived task: owns the stdio_client + ClientSession for its lifetime.

        Enters and exits both context managers in THIS task to avoid anyio
        cancel-scope cross-task errors.
        """
        import os
        # Merge extra env onto current env so PATH etc. are preserved
        merged_env = {**os.environ, **self._extra_env} if self._extra_env else None

        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=merged_env,
        )

        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await self._cache_tools(session)
                    self._ready.set()

                    logger.info(
                        f"MCP '{self._server_name}' ready: "
                        f"{[t.name for t in self._tools]}"
                    )

                    # Request loop — process until _STOP sentinel received
                    while True:
                        item = await self._request_queue.get()
                        if item is _STOP:
                            break
                        tool_name, arguments, future = item
                        if future.cancelled():
                            continue
                        try:
                            # Timeout INSIDE the loop too: the caller-side wait_for
                            # only unblocks the caller; without this a genuinely hung
                            # call_tool would wedge the single session loop and back up
                            # every subsequent request on this server.
                            result = await asyncio.wait_for(
                                session.call_tool(tool_name, arguments),
                                timeout=self._call_timeout,
                            )
                            texts = []
                            for block in result.content:
                                texts.append(block.text if hasattr(block, "text") else str(block))
                            if not future.done():
                                future.set_result(("\n".join(texts) or "完成", False))
                        except asyncio.TimeoutError:
                            logger.warning(f"MCP call timeout [{self._server_name}/{tool_name}] > {self._call_timeout}s")
                            if not future.done():
                                future.set_result((f"[MCP超时] 工具 '{tool_name}' 执行超过 {self._call_timeout} 秒", True))
                        except Exception as e:
                            logger.error(f"MCP call error [{self._server_name}/{tool_name}]: {e}")
                            if not future.done():
                                future.set_result((f"[MCP执行错误] {e}", True))
        except Exception as e:
            logger.error(f"MCP session loop failed [{self._server_name}]: {e}")
            # Fail any pending requests
            if self._request_queue:
                while not self._request_queue.empty():
                    try:
                        item = self._request_queue.get_nowait()
                        if item is not _STOP:
                            _, _, future = item
                            if not future.done():
                                future.set_result((f"[MCP断开] {e}", True))
                    except asyncio.QueueEmpty:
                        break
            raise

    async def _cache_tools(self, session: ClientSession) -> None:
        """Fetch and cache tool list from the server, applying allow_list filter."""
        tools_result = await session.list_tools()
        self._tools = []
        for t in tools_result.tools:
            # allow_list: skip tools not in the whitelist
            if self._allow_list is not None and t.name not in self._allow_list:
                logger.debug(f"MCP '{self._server_name}': skipping '{t.name}' (not in allow_list)")
                continue
            exposed_name = _mcp_tool_name(self._server_name, t.name)
            params_schema: dict = {}
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

    async def close(self) -> None:
        """Signal the session task to stop and wait for it to exit cleanly."""
        if self._closed:
            return
        self._closed = True
        if self._request_queue is not None:
            await self._request_queue.put(_STOP)
        if self._session_task and not self._session_task.done():
            try:
                await asyncio.wait_for(self._session_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._session_task.cancel()
        self._tools = []
        self._session_task = None
        self._request_queue = None
        logger.info(f"MCP provider '{self._server_name}' closed.")

    # ------------------------------------------------------------------ #
    # Factory: load from mcp_servers.json
    # ------------------------------------------------------------------ #

    @classmethod
    def from_config(cls, config_path: str | Path) -> list["McpClientToolProvider"]:
        """
        Parse mcp_servers.json and return one provider per enabled server.

        Config format (compatible with Claude Desktop):
        {
          "mcpServers": {
            "filesystem": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"],
              "env": {},           // merged onto os.environ; missing = use host env
              "allow_list": ["read_file", "write_file"],  // optional tool whitelist
              "call_timeout": 30,  // optional per-call timeout in seconds
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
            allow_list = spec.get("allow_list")
            approval_tools = spec.get("approval_tools")
            providers.append(cls(
                server_name=name,
                command=spec["command"],
                args=spec.get("args", []),
                env=spec.get("env") or {},
                allow_list=set(allow_list) if allow_list else None,
                call_timeout=spec.get("call_timeout", _DEFAULT_CALL_TIMEOUT),
                require_approval_all=spec.get("require_approval_all", False),
                approval_tools=set(approval_tools) if approval_tools else None,
            ))
        return providers
