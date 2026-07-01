"""Per-process bridge between the worker's IPC channel and McpProxyProvider.

MCP runs in the cross-group collector process; a worker reaches it over the bus.
This module is the worker-side glue (a per-process singleton):

  - the Worker installs a `send` callable (builds + sends an MCP_CALL frame
    upstream) and the worker's id, and feeds it the schema snapshots + results
    it receives downstream.
  - McpProxyProvider calls `request()` to execute an MCP tool: it allocates a
    request_id, sends the call, and awaits the matching MCP_RESULT future.

No runtime/ipc import here (the Worker injects the IPC-aware send), so executors
stays free of a runtime import cycle.
"""
import asyncio

# Default per-call timeout for an MCP round-trip over the bus (seconds).
_DEFAULT_TIMEOUT = 35


class MCPBridge:
    def __init__(self):
        self._send = None                 # async (rid, name, args, group_id, trace_id) -> None
        self._send_auth = None            # async (rid, server, group_id, trace_id) -> None
        self._origin = None               # this worker's id
        self._pending: dict[str, asyncio.Future] = {}
        self.schemas: list = []           # latest MCP tool-schema snapshot (OpenAI format)
        self._by_name: dict[str, dict] = {}   # name -> schema, for O(1) lookup (#6)
        self._seq = 0

    def install(self, send, origin: str) -> None:
        self._send = send
        self._origin = origin

    def install_auth(self, send_auth) -> None:
        self._send_auth = send_auth

    def reset(self) -> None:
        self._send = None
        self._send_auth = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(("[MCP错误] 总线已断开", True))
        self._pending.clear()

    def set_schemas(self, schemas) -> None:
        self.schemas = schemas or []
        self._by_name = {
            s["function"]["name"]: s
            for s in self.schemas
            if isinstance(s.get("function"), dict) and s["function"].get("name")
        }

    def schema_for(self, name: str) -> dict | None:
        return self._by_name.get(name)

    def is_ready(self) -> bool:
        return self._send is not None

    def resolve(self, request_id: str, result: str, is_error: bool) -> None:
        fut = self._pending.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result((result, is_error))

    def _finish_pending(self, request_id: str, fut: asyncio.Future) -> None:
        current = self._pending.get(request_id)
        if current is fut:
            self._pending.pop(request_id, None)

    async def request(self, name: str, arguments: dict, *, group_id, trace_id,
                      timeout: int = _DEFAULT_TIMEOUT) -> tuple[str, bool]:
        if self._send is None:
            return "[MCP错误] collector 总线未就绪", True
        self._seq += 1
        rid = f"{self._origin}-{self._seq}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send(rid, name, arguments, group_id, trace_id)
        except asyncio.CancelledError:
            self._finish_pending(rid, fut)
            raise
        except Exception as e:
            self._finish_pending(rid, fut)
            return f"[MCP错误] 发送失败: {e}", True
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.CancelledError:
            self._finish_pending(rid, fut)
            raise
        except asyncio.TimeoutError:
            self._finish_pending(rid, fut)
            return f"[MCP超时] 工具 '{name}' 超过 {timeout} 秒", True
        finally:
            self._finish_pending(rid, fut)

    async def authenticate(self, server: str, *, group_id, trace_id,
                           timeout: int = 60) -> tuple[str, bool]:
        """Start OAuth for a server; the collector replies (via MCP_RESULT) with
        the authorization URL to surface to the user."""
        if self._send_auth is None:
            return "[MCP认证错误] collector 总线未就绪", True
        self._seq += 1
        rid = f"{self._origin}-auth-{self._seq}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send_auth(rid, server, group_id, trace_id)
        except asyncio.CancelledError:
            self._finish_pending(rid, fut)
            raise
        except Exception as e:
            self._finish_pending(rid, fut)
            return f"[MCP认证错误] 发送失败: {e}", True
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.CancelledError:
            self._finish_pending(rid, fut)
            raise
        except asyncio.TimeoutError:
            self._finish_pending(rid, fut)
            return f"[MCP认证超时] 服务器 '{server}' 超过 {timeout} 秒", True
        finally:
            self._finish_pending(rid, fut)


# Per-process singleton.
bridge = MCPBridge()
