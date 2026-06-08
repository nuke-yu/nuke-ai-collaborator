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


def _kill_descendants() -> int:
    """Hard-kill orphaned descendant processes on collector shutdown.

    The SDK terminates the immediate stdio child on context exit, but a launcher
    like `npx` can leave its `node` grandchild orphaned. The collector owns ALL
    MCP subprocesses, so on shutdown every descendant is fair game (process-tree
    SIGTERM → SIGKILL). Returns how many were swept. No-op if psutil is absent."""
    import os
    try:
        import psutil
    except Exception:
        return 0
    try:
        children = psutil.Process(os.getpid()).children(recursive=True)
    except Exception:
        return 0
    for c in children:
        try:
            c.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(children, timeout=3)
    for c in alive:
        try:
            c.kill()
        except Exception:
            pass
    return len(children)


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
        self._auth_inflight: set = set()       # servers with an in-progress OAuth flow

    async def _init_providers(self) -> None:
        import os
        from executors.tool_router import ToolRouter
        from executors.providers.mcp_client import McpClientToolProvider
        self._router = ToolRouter()
        # Config path is env-overridable (deployments / tests that want no MCP).
        cfg = os.environ.get("MCP_SERVERS_CONFIG") or (Path(__file__).parent.parent / "mcp_servers.json")
        for prov in McpClientToolProvider.from_config(cfg):
            server = prov.provider_id.removeprefix("mcp:")
            try:
                if prov.oauth_cfg and prov.url:
                    # OAuth server: attach the auth provider so stored tokens are
                    # used/refreshed. Only auto-connect at startup if we already
                    # have a token — a token-less server must NOT block startup on
                    # an interactive flow; it's deferred to mcp_authenticate.
                    from executors.providers.mcp_oauth_store import MCPTokenStorage
                    prov.set_auth(await self._build_auth_provider(prov, server))
                    if await MCPTokenStorage(server).get_tokens() is None:
                        self._router.register_provider(prov)   # known, not yet connected
                        log.info("collector: oauth server '%s' deferred (run mcp_authenticate)", server)
                        continue
                await prov.initialize()
                self._router.register_provider(prov)
                log.info("collector: MCP provider '%s' ready", prov.provider_id)
            except Exception as e:
                # Keep the provider registered so mcp_authenticate can retry it.
                if prov not in self._router._providers:
                    self._router.register_provider(prov)
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
        import hashlib
        import json
        schemas = self._schemas()
        # Content-aware signature: a tool changing its params/description (same
        # name) must still re-push. Names-only diff would miss it AND the periodic
        # loop uses this same check, so it would never propagate.
        payload_str = json.dumps(
            sorted(schemas, key=lambda x: x.get("function", {}).get("name", "")),
            sort_keys=True, default=str,
        )
        sig = hashlib.sha256(payload_str.encode()).hexdigest()
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

    async def _build_auth_provider(self, prov, server: str):
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata, OAuthClientInformationFull
        from executors.providers.mcp_oauth_store import MCPTokenStorage
        oauth = prov.oauth_cfg if isinstance(prov.oauth_cfg, dict) else {}
        storage = MCPTokenStorage(server)
        callback = self._callback_url()
        # Pre-registered OAuth app: seed client info so the SDK skips RFC 7591
        # dynamic registration (servers like GitHub require a pre-registered
        # client_id/secret). Only seed once; stored info wins on later runs.
        if oauth.get("client_id") and await storage.get_client_info() is None:
            await storage.set_client_info(OAuthClientInformationFull(
                client_id=oauth["client_id"],
                client_secret=oauth.get("client_secret"),
                redirect_uris=[callback],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope=oauth.get("scope"),
            ))
        meta = OAuthClientMetadata(
            redirect_uris=[callback],
            client_name="nuke-ai-collaborator",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=oauth.get("scope"),
        )
        return OAuthClientProvider(
            server_url=prov.url,
            client_metadata=meta,
            storage=storage,
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
        finally:
            self._auth_inflight.discard(server)   # flow ended (success or fail)

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
        # Per-server guard: a concurrent MCP_AUTH_START for the same server would
        # race set_auth()/initialize(). Check-and-set is atomic (no await between).
        if server in self._auth_inflight:
            await reply(f"[MCP认证] '{server}' 的授权正在进行中，请使用之前返回的链接完成", True)
            return
        self._auth_inflight.add(server)
        spawned = False
        try:
            prov.set_auth(await self._build_auth_provider(prov, server))
            url_fut = self._flows.begin(server)
            t = asyncio.create_task(self._reinit_with_auth(prov, server))
            self._tasks.add(t); t.add_done_callback(self._tasks.discard)
            spawned = True   # the reinit task now owns releasing _auth_inflight
            url = await asyncio.wait_for(url_fut, timeout=60)
            await reply(f"请在浏览器打开以下链接完成 '{server}' 的授权，完成后工具会自动可用：\n{url}", False)
        except Exception as e:
            self._flows.fail(server, str(e))
            await reply(f"[MCP认证错误] {e}", True)
        finally:
            if not spawned:
                self._auth_inflight.discard(server)   # flow never started → release now

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
            try:
                from executors.providers.mcp_oauth_store import aclose_all
                await aclose_all()            # close shared OAuth token-store connections
            except Exception:
                pass
            swept = _kill_descendants()       # reap orphaned npx/node grandchildren
            if swept:
                log.info("collector: swept %d residual subprocess(es) on shutdown", swept)


async def run_collector(addr: str) -> None:
    await MCPCollector(addr).run()
