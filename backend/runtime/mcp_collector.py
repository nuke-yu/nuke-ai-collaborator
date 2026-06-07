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
        return self._router.get_external_schemas() if self._router else []

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
                if frame.get("type") == ipc.protocol.MCP_CALL:
                    t = asyncio.create_task(self._handle_call(frame))
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
                else:
                    log.debug("collector: unhandled frame type=%s", frame.get("type"))
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
