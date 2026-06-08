"""MCP collector — the single cross-group process that owns all MCP connections.

MCP is a shared capability, not per-group, so it runs in ONE collector process
instead of being re-spawned inside every worker. The Supervisor is the bus:

    worker ──MCP_CALL──▶ supervisor ──▶ collector ──▶ real MCP server(s)
    worker ◀─MCP_RESULT── supervisor ◀── collector ◀── real MCP server(s)

The collector connects to the Supervisor like a worker (HELLO with
worker_id=MCP_COLLECTOR_ID), owns the McpClientToolProvider instances in its own
ToolRouter, executes MCP_CALL requests, and pushes the current tool-schema
snapshot (MCP_SCHEMAS) on startup and whenever the tool list changes.

Trust boundary: permission/HIL runs on the WORKER side before a call is sent, so
the collector executes pre-authorized calls (context _pre_authorized=True). It
still applies the untrusted-result fence + secret redaction (inside the
provider) before results cross back over the bus.
"""
import asyncio
import logging
from pathlib import Path

from runtime import ipc

log = logging.getLogger(__name__)

# Cheap diff-based schema re-push so ToolListChanged (a server adding/removing
# tools) propagates to workers without per-call round-trips.
_SCHEMA_REPUSH_INTERVAL = 10  # seconds


class MCPCollector:
    def __init__(self, addr: str):
        self.addr = addr
        self._reader = None
        self._writer = None
        self._router = None
        self._tasks: set = set()
        self._last_schema_sig = None
        from executors.providers.mcp_auth_flows import MCPAuthFlows
        self._flows = MCPAuthFlows()

    async def _init_providers(self) -> None:
        import os
        from executors.tool_router import ToolRouter
        from executors.providers.mcp_client import McpClientToolProvider
        self._router = ToolRouter()
        # Config path is env-overridable (deployments / tests that want no MCP).
        cfg = os.environ.get("MCP_SERVERS_CONFIG") or (Path(__file__).parent.parent / "mcp_servers.json")
        for prov in McpClientToolProvider.from_config(cfg):
            try:
                await prov.initialize()
                self._router.register_provider(prov)
                log.info("collector: MCP provider '%s' ready", prov.provider_id)
            except Exception as e:
                log.warning("collector: MCP init failed [%s]: %s", prov.provider_id, e)

    def _schemas(self) -> list:
        """Merged MCP tool schemas across all servers, each annotated with a
        per-server `needs_approval` flag (extra key; ignored when the worker
        builds the LLM schema, read by the proxy's HIL gate)."""
        if not self._router:
            return []
        schemas = self._router.get_external_schemas()
        flags: dict[str, bool] = {}
        for p in self._router._providers:
            if hasattr(p, "approval_flags"):
                try:
                    flags.update(p.approval_flags())
                except Exception:
                    pass
        for s in schemas:
            name = s.get("function", {}).get("name")
            if name in flags:
                s["needs_approval"] = flags[name]
        return schemas

    async def connect(self) -> None:
        self._reader, self._writer = await ipc.connect(self.addr)
        await ipc.send_msg(self._writer, {
            "type": ipc.protocol.HELLO,
            "worker_id": ipc.protocol.MCP_COLLECTOR_ID,
        })
        log.info("collector: connected to supervisor at %s", self.addr)

    async def _push_schemas(self) -> None:
        schemas = self._schemas()
        sig = tuple(sorted(s["function"]["name"] for s in schemas))
        if sig == self._last_schema_sig:
            return
        self._last_schema_sig = sig
        await ipc.send_msg(self._writer, ipc.protocol.envelope(
            ipc.protocol.MCP_SCHEMAS, group_id=0, payload={"schemas": schemas},
        ))
        log.info("collector: pushed %d MCP schema(s) to bus", len(schemas))

    async def _repush_loop(self) -> None:
        while True:
            await asyncio.sleep(_SCHEMA_REPUSH_INTERVAL)
            try:
                await self._push_schemas()
            except Exception:
                log.exception("collector: schema re-push failed")

    async def _handle_call(self, frame: dict) -> None:
        rid = frame.get("request_id")
        origin = frame.get("origin_worker_id")
        name = frame.get("tool")
        arguments = frame.get("arguments") or {}
        gid = frame.get("group_id")
        tid = frame.get("trace_id")
        try:
            result, is_error = await self._router.execute(
                name, arguments,
                context={"_pre_authorized": True, "group_id": gid, "trace_id": tid},
            )
        except Exception as e:
            result, is_error = f"[MCP collector 错误] {e}", True
        try:
            await ipc.send_msg(self._writer, ipc.protocol.envelope(
                ipc.protocol.MCP_RESULT, group_id=gid, trace_id=tid,
                request_id=rid, origin_worker_id=origin,
                result=result, is_error=is_error,
            ))
        except Exception:
            log.exception("collector: failed to send MCP_RESULT rid=%s", rid)

    # ── OAuth (McpAuthTool style) ──────────────────────────────────────────
    def _callback_url(self) -> str:
        import os
        base = (os.environ.get("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
        return f"{base}/mcp/oauth/callback"

    def _find_provider(self, server: str):
        if not self._router:
            return None
        for p in self._router._providers:
            if getattr(p, "provider_id", None) == f"mcp:{server}":
                return p
        return None

    def _build_auth_provider(self, prov, server: str):
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata
        from executors.providers.mcp_oauth_store import MCPTokenStorage
        oauth = prov.oauth_cfg if isinstance(prov.oauth_cfg, dict) else {}
        meta = OAuthClientMetadata(
            redirect_uris=[self._callback_url()],
            client_name="nuke-ai-collaborator",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=oauth.get("scope"),
        )
        return OAuthClientProvider(
            server_url=prov.url,
            client_metadata=meta,
            storage=MCPTokenStorage(server),
            redirect_handler=self._flows.redirect_handler_for(server),
            callback_handler=self._flows.callback_handler_for(server),
        )

    async def _reinit_with_auth(self, prov, server: str) -> None:
        """(Re)connect a server with its OAuth provider — runs the interactive
        flow (redirect → callback). On success the server's tools load → re-push."""
        try:
            if prov.is_initialized():
                await prov.close()
            await prov.initialize()
            await self._push_schemas()
            log.info("collector: OAuth complete for '%s', tools loaded", server)
        except Exception as e:
            log.warning("collector: OAuth init failed for '%s': %s", server, e)
            self._flows.fail(server, str(e))

    async def _handle_auth_start(self, frame: dict) -> None:
        rid = frame.get("request_id")
        origin = frame.get("origin_worker_id")
        server = frame.get("server")
        gid = frame.get("group_id")
        tid = frame.get("trace_id")

        async def reply(result, is_error):
            await ipc.send_msg(self._writer, ipc.protocol.envelope(
                ipc.protocol.MCP_RESULT, group_id=gid, trace_id=tid,
                request_id=rid, origin_worker_id=origin, result=result, is_error=is_error,
            ))

        prov = self._find_provider(server)
        if prov is None or not prov.url:
            await reply(f"[MCP认证] 未找到 remote server '{server}'（仅 remote+oauth 适用）", True)
            return
        try:
            prov.set_auth(self._build_auth_provider(prov, server))
            url_fut = self._flows.begin(server)
            t = asyncio.create_task(self._reinit_with_auth(prov, server))
            self._tasks.add(t); t.add_done_callback(self._tasks.discard)
            url = await asyncio.wait_for(url_fut, timeout=60)
            await reply(f"请在浏览器打开以下链接完成 '{server}' 的授权，完成后工具会自动可用：\n{url}", False)
        except Exception as e:
            self._flows.fail(server, str(e))
            await reply(f"[MCP认证错误] {e}", True)

    async def run(self) -> None:
        await self._init_providers()
        await self.connect()
        await self._push_schemas()
        repush = asyncio.create_task(self._repush_loop())
        try:
            while True:
                frame = await ipc.recv_msg(self._reader)
                if frame is None:
                    break
                ftype = frame.get("type")
                if ftype == ipc.protocol.MCP_CALL:
                    t = asyncio.create_task(self._handle_call(frame))
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
                elif ftype == ipc.protocol.MCP_AUTH_START:
                    t = asyncio.create_task(self._handle_auth_start(frame))
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
                elif ftype == ipc.protocol.MCP_OAUTH_CALLBACK:
                    self._flows.resolve_callback(frame.get("state"), frame.get("code"))
                else:
                    log.debug("collector: unhandled frame type=%s", ftype)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            log.info("collector: supervisor connection closed")
        finally:
            repush.cancel()
            if self._router:
                await self._router.close_all()
            if self._writer:
                self._writer.close()


async def run_collector(addr: str) -> None:
    await MCPCollector(addr).run()
