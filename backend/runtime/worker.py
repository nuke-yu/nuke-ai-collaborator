"""CELL-10: Worker runtime loop (Project-Cell Isolation V3).

A Worker is a long-lived process owning a shard of groups. It connects to the
Supervisor over IPC and runs two pumps:

  downstream  Supervisor → Worker : user_message / abort / permission_response /
              wake_trigger. user_message binds the group's private DB and calls
              the dispatch handler.
  upstream    Worker → Supervisor : every bus event the dispatch produces is
              wrapped as a `broadcast` frame and forwarded (this replaces
              bus/adapter.ws_adapter — the sink is the IPC tunnel, not WSManager).

The bus is the per-process global singleton (module globals are per-process, so a
worker process naturally has its own bus). The dispatch handler is injectable; the
production wiring (load context from the split DBs + run dispatch_bots) is fleshed
out / exercised in CELL-12. This module is the loop + lifecycle + routing.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import db
from runtime import tracing
from runtime import ipc
from runtime.dbpaths import group_db_path
import permissions
from core import bg

log = logging.getLogger(__name__)

_STARTUP_HYDRATION_RETRIES = 15


def _is_retryable_startup_db_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "no such table" in msg or "locked" in msg


class Worker:
    def __init__(self, worker_id, addr, *, bus=None, dispatch=None, on_abort=None,
                 hydrate_assigned_groups: bool = True):
        self.worker_id = worker_id
        self.addr = addr
        if bus is None:
            from bus import bus as _global_bus
            bus = _global_bus
        self.bus = bus
        self._dispatch = dispatch or self._default_dispatch
        self._on_abort = on_abort
        self._reader = None
        self._writer = None
        self._sub = None
        self._upstream_task = None
        self._report_task = None
        self._recap_task = None
        self._compaction_task = None
        self._hydration_task = None
        self._hydrate_assigned_groups_enabled = hydrate_assigned_groups

    async def connect(self) -> None:
        self._reader, self._writer = await ipc.connect(self.addr)
        # Identify ourselves so the Supervisor can route group→worker (CELL-12).
        await ipc.send_msg(self._writer, {"type": ipc.protocol.HELLO, "worker_id": self.worker_id})
        # Wire the MCP bridge: McpProxyProvider sends MCP_CALL upstream through
        # this connection and awaits the matching MCP_RESULT (collector on the bus).
        from executors.mcp_bridge import bridge as _mcp_bridge
        async def _send_mcp_call(rid, name, arguments, group_id, trace_id):
            await ipc.send_msg(self._writer, ipc.protocol.envelope(
                ipc.protocol.MCP_CALL, group_id=group_id or 0, trace_id=trace_id,
                request_id=rid, origin_worker_id=self.worker_id,
                tool=name, arguments=arguments,
            ))
        async def _send_mcp_auth(rid, server, group_id, trace_id):
            await ipc.send_msg(self._writer, ipc.protocol.envelope(
                ipc.protocol.MCP_AUTH_START, group_id=group_id or 0, trace_id=trace_id,
                request_id=rid, origin_worker_id=self.worker_id, server=server,
            ))
        _mcp_bridge.install(send=_send_mcp_call, origin=self.worker_id)
        _mcp_bridge.install_auth(_send_mcp_auth)
        # Register the wildcard subscription synchronously BEFORE we start
        # processing downstream messages, so no early bus event is missed.
        self._sub = self.bus.subscribe_all()
        self._upstream_task = asyncio.create_task(self._pump_upstream())
        self._report_task = asyncio.create_task(self._report_stats_loop())
        self._recap_task = asyncio.create_task(self._pump_recap())
        self._compaction_task = asyncio.create_task(self._pump_compaction())
        if self._hydrate_assigned_groups_enabled:
            self._hydration_task = asyncio.create_task(self._hydrate_assigned_groups())

    async def run(self) -> None:
        tracing.setup_structured_logging(log_file=f"logs/worker-{self.worker_id}.log")
        if self._writer is None:
            await self.connect()
        try:
            while True:
                msg = await ipc.recv_msg(self._reader)
                if msg is None: break
                await self._handle(msg)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            log.info("worker %s: downstream closed", self.worker_id)
        finally:
            await self.close()

    
    async def _report_stats_loop(self) -> None:
        """CELL-20: Periodically push local metrics to Supervisor."""
        from core import bg
        import permissions
        from runtime.lifecycle import manager as lifecycle
        
        while True:
            try:
                stats = {
                    "bg": bg.stats(),
                    "permissions": permissions.pending_stats(),
                    "lifecycle": lifecycle.stats(),
                    "worker_id": self.worker_id,
                }
                await ipc.send_msg(self._writer, ipc.protocol.envelope(
                    ipc.protocol.STATS_REPORT, group_id=0, payload=stats
                ))
            except Exception:
                log.exception("worker %s: failed to report stats", self.worker_id)
            await asyncio.sleep(30)

    async def close(self) -> None:
        # Fail any in-flight MCP calls instead of leaving them awaiting forever.
        try:
            from executors.mcp_bridge import bridge as _mcp_bridge
            _mcp_bridge.reset()
        except Exception:
            log.exception("worker %s: failed to reset MCP bridge during close", self.worker_id)

        to_cancel = []
        if self._report_task:
            self._report_task.cancel()
            to_cancel.append(self._report_task)
        if self._upstream_task:
            self._upstream_task.cancel()
            to_cancel.append(self._upstream_task)
        if self._recap_task:
            self._recap_task.cancel()
            to_cancel.append(self._recap_task)
        if self._compaction_task:
            self._compaction_task.cancel()
            to_cancel.append(self._compaction_task)
        if self._hydration_task:
            self._hydration_task.cancel()
            to_cancel.append(self._hydration_task)
        self._report_task = None
        self._upstream_task = None
        self._recap_task = None
        self._compaction_task = None
        self._hydration_task = None
        if to_cancel:
            await asyncio.gather(*to_cancel, return_exceptions=True)
        if self._writer:
            self._writer.close()
            wait_closed = getattr(self._writer, "wait_closed", None)
            if callable(wait_closed):
                try:
                    await wait_closed()
                except Exception:
                    log.exception("worker %s: writer wait_closed failed during close", self.worker_id)
            self._writer = None

    async def _recap_on_paused(self, gid: int) -> None:
        """Handle one WorkflowPaused event: generate a recap only for a group this
        worker actually owns, with the group DB bound. Best-effort."""
        from runtime.lifecycle import manager as lifecycle
        if not lifecycle.is_active(gid):
            return
        try:
            from runtime.dbpaths import group_db_path
            with db.bind_db(group_db_path(gid)):
                from core.recap import generate_and_cache_recap
                await generate_and_cache_recap(gid)
        except Exception:
            log.exception("worker %s: failed to generate event-driven recap for group %s", self.worker_id, gid)

    async def _retro_on_done(self, gid: int) -> None:
        """Handle one WorkflowPaused(reason='done') event: generate retrospective for group."""
        from runtime.lifecycle import manager as lifecycle
        if not lifecycle.is_active(gid):
            return
        try:
            from runtime.dbpaths import group_db_path
            with db.bind_db(group_db_path(gid)):
                from core.recap.retro import generate_ticket_retrospective
                await generate_ticket_retrospective(gid)
        except Exception:
            log.exception("worker %s: failed to generate retrospective for group %s", self.worker_id, gid)

    async def _pump_recap(self) -> None:
        from bus.events import WorkflowPaused
        sub = self.bus.subscribe(WorkflowPaused)
        async with sub:
            async for ev in sub:
                if ev.reason == "done":
                    bg.spawn(self._retro_on_done(ev.group_id))
                else:
                    bg.spawn(self._recap_on_paused(ev.group_id))

    async def _pump_compaction(self) -> None:
        from bus.events import CompactionTriggered
        sub = self.bus.subscribe(CompactionTriggered)
        async with sub:
            async for ev in sub:
                gid = ev.group_id
                from runtime.lifecycle import manager as lifecycle
                if lifecycle.is_active(gid):
                    async def _run():
                        try:
                            from runtime.dbpaths import group_db_path
                            db_path = group_db_path(gid)
                            with db.bind_db(db_path):
                                from executors.compact import maybe_compact_db_history
                                await maybe_compact_db_history(
                                    gid, ev.bot_id, ev.provider, ev.model_name, ev.temperature, self.bus
                                )
                        except Exception:
                            log.exception("worker %s: failed to execute event-driven compaction for group %s", self.worker_id, gid)
                    bg.spawn(_run())

    async def _pump_upstream(self) -> None:
        async with self._sub as sub:
            async for payload in sub:
                gid = payload.get("group_id")
                if gid is None:
                    continue  # un-routable events aren't forwarded to the supervisor
                # A single failed IPC send (e.g. transient backpressure / half-open
                # write) must NOT kill the whole pump — otherwise the worker stays
                # alive but silently stops forwarding ALL future bus events and the
                # frontend goes dark. Drop the one event, log it, keep pumping.
                try:
                    await ipc.send_msg(self._writer, ipc.protocol.envelope(
                        ipc.protocol.BROADCAST, group_id=gid, payload=payload,
                    ))
                except Exception:
                    log.exception(
                        "worker %s: upstream send failed, dropping event type=%s group=%s",
                        self.worker_id, payload.get("type"), gid,
                    )

    
    
    @asynccontextmanager
    async def _group_context(self, gid: int):
        from runtime.lifecycle import manager as lifecycle
        db_path = await lifecycle.hydrate(gid)
        with db.bind_db(db_path):
            yield

    async def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        gid = msg.get("group_id")
        tid = msg.get("trace_id")
        
        if gid is not None and msg.get("lang"):
            from workspace.layout import set_group_language
            set_group_language(gid, msg.get("lang"))
            
        with tracing.trace_context(trace_id=tid, group_id=gid):
            if t == ipc.protocol.ABORT:
                if self._on_abort:
                    self._on_abort(gid)
                else:
                    bg.abort_group(gid)
                return

            if t == ipc.protocol.ABORT_REQUEST:
                # P0-2: Abort with ACK protocol
                request_id = msg.get("request_id", "")
                mode = msg.get("mode", "abort")
                await self._handle_abort_request(gid, request_id, mode, tid)
                return

            if t == ipc.protocol.RELEASE_LEASE:
                from runtime.lifecycle import manager as lifecycle
                await lifecycle.evict(gid)
                await ipc.send_msg(self._writer, ipc.protocol.envelope(
                    ipc.protocol.LEASE_RELEASED, group_id=gid, trace_id=tid, worker_id=self.worker_id
                ))
                return

            if t == ipc.protocol.MCP_SCHEMAS:
                # Collector pushed a new MCP tool-schema snapshot (not group-scoped).
                from executors.mcp_bridge import bridge as _mcp_bridge
                _mcp_bridge.set_schemas((msg.get("payload") or {}).get("schemas", []))
                return

            if t == ipc.protocol.MCP_RESULT:
                # Reply to an MCP tool call this worker dispatched to the collector.
                from executors.mcp_bridge import bridge as _mcp_bridge
                _mcp_bridge.resolve(msg.get("request_id"), msg.get("result"), bool(msg.get("is_error")))
                return

            if t == ipc.protocol.PERMISSION_RESPONSE:
                request_id = msg.get("request_id", "")
                approved = bool(msg.get("approved", False))
                persistence = msg.get("persistence", "once")
                req = permissions.resolve(request_id, approved, persistence, group_id=gid)
                if req and approved and persistence == "always":
                    # Single authority for "always" persistence (all tools incl MCP).
                    # Scope the rule to the kind of call approved (#5) instead of a
                    # blanket allow-all-args — synthesized from the pending call's
                    # tool + arguments.
                    args_pattern = permissions.synthesize_args_pattern(req.tool_name, req.arguments)
                    async with self._group_context(gid):
                        bg.spawn(permissions.save_rule(req.bot_id, req.tool_name, args_pattern, "allow"))
                return

            async with self._group_context(gid):
                if t == ipc.protocol.USER_MESSAGE:
                    await self._dispatch(msg)
                elif t == ipc.protocol.CONFIRM:
                    import core.workflow as wf
                    await wf.confirm(gid, msg.get("gate_id"),
                                     revise=bool(msg.get("revise", False)),
                                     note=(msg.get("note") or ""))
                elif t == ipc.protocol.START_WORKFLOW:
                    from runtime.dispatch import dispatch_start_workflow
                    await dispatch_start_workflow(msg)
                elif t == ipc.protocol.WORKFLOW_NEXT:
                    from runtime.dispatch import dispatch_workflow_next
                    await dispatch_workflow_next(msg)
                elif t == ipc.protocol.WORKFLOW_END:
                    from runtime.dispatch import dispatch_workflow_end
                    await dispatch_workflow_end(msg)
                elif t == ipc.protocol.QUERY:
                    from runtime.query_dispatch import dispatch_query
                    await dispatch_query(msg)
                elif t == ipc.protocol.MUTATE:
                    from runtime.query_dispatch import dispatch_mutate
                    await dispatch_mutate(msg)
                elif t == ipc.protocol.WAKE_TRIGGER:
                    from runtime.dispatch import dispatch_wake_trigger
                    await dispatch_wake_trigger(msg)
                else:
                    log.debug("worker %s: unhandled downstream type=%s", self.worker_id, t)

    async def _handle_abort_request(self, gid: int, request_id: str, mode: str, tid: str) -> None:
        """P0-2: Handle ABORT_REQUEST with ACK protocol.

        Steps:
          1. Cancel the group's tasks via bg.abort_group()
          2. Wait for cancelled tasks to complete cleanup (gather with return_exceptions)
          3. Send ABORT_ACK back to Supervisor with result

        Args:
            gid: Group ID
            request_id: Request ID for tracking
            mode: "abort" or "retry"
            tid: Trace ID for logging
        """
        cancelled_count = 0
        cleanup_status = "success"
        error = None

        try:
            # Step 1: Cancel tasks
            if self._on_abort:
                self._on_abort(gid)
            else:
                cancelled_count = bg.abort_group(gid)

            log.info(
                "worker %s: abort_request group=%d request_id=%s cancelled=%d",
                self.worker_id, gid, request_id, cancelled_count,
            )

            # Step 2: Wait for cancelled tasks to complete cleanup
            # bg.abort_group() cancels tasks, but we need to wait for their finally blocks
            # to run (worktree cleanup, etc.)
            tasks = bg._group_tasks.get(gid, set())
            if tasks:
                # Wait up to 5 seconds for cleanup to complete
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    log.warning(
                        "worker %s: abort_request cleanup timeout for group %d",
                        self.worker_id, gid,
                    )
                    cleanup_status = "partial"

            # Step 3: Send ACK
            await ipc.send_msg(
                self._writer,
                ipc.protocol.envelope(
                    ipc.protocol.ABORT_ACK,
                    group_id=gid,
                    trace_id=tid,
                    request_id=request_id,
                    cancelled_count=cancelled_count,
                    cleanup_status=cleanup_status,
                    error=error,
                ),
            )

        except Exception as e:
            # Fail closed: send ACK with error status
            log.exception(
                "worker %s: abort_request failed for group %d: %s",
                self.worker_id, gid, e,
            )
            try:
                await ipc.send_msg(
                    self._writer,
                    ipc.protocol.envelope(
                        ipc.protocol.ABORT_ACK,
                        group_id=gid,
                        trace_id=tid,
                        request_id=request_id,
                        cancelled_count=0,
                        cleanup_status="failed",
                        error=str(e),
                    ),
                )
            except Exception as send_err:
                log.error(
                    "worker %s: failed to send ABORT_ACK for group %d: %s",
                    self.worker_id, gid, send_err,
                )

    async def _default_dispatch(self, msg: dict) -> None:
        raise NotImplementedError(
            "Worker dispatch not wired — injected by the Supervisor integration (CELL-12)"
        )

    async def _hydrate_assigned_groups(self) -> None:
        """Query the central DB for all groups assigned to this worker, and hydrate them."""
        import sqlite3
        import db
        from runtime.lifecycle import manager as lifecycle

        # Retry loop to dynamically gate on database initialization/readiness
        backoff = 0.1
        rows = None
        for attempt in range(_STARTUP_HYDRATION_RETRIES):
            try:
                async with db.global_db() as cdb:
                    async with cdb.execute("SELECT id FROM groups WHERE assigned_worker_id = ?", (self.worker_id,)) as cur:
                        rows = await cur.fetchall()
                break
            except sqlite3.OperationalError as e:
                # Retry on transient startup errors (e.g. no such table, database is locked)
                if _is_retryable_startup_db_error(e):
                    log.info("worker %s: central DB not yet initialized or locked (attempt %d/15), retrying in %.2fs...",
                             self.worker_id, attempt + 1, backoff)
                    if attempt + 1 == _STARTUP_HYDRATION_RETRIES:
                        break
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 2.0)
                    continue
                log.exception("worker %s: database operational error during startup hydration", self.worker_id)
                return
            except Exception:
                log.exception("worker %s: unexpected query failure during startup hydration", self.worker_id)
                return

        if rows is None:
            log.error("worker %s: failed to query assigned groups after retries (DB schema not ready)", self.worker_id)
            return

        for (gid,) in rows:
            log.info("worker %s: resuming assigned group %d on startup", self.worker_id, gid)
            try:
                await lifecycle.hydrate(gid)
            except Exception:
                if getattr(lifecycle, "_shutting_down", False):
                    log.info("worker %s: stopping startup hydration because lifecycle is shutting down", self.worker_id)
                    return
                log.exception("worker %s: failed to hydrate assigned group %d on startup", self.worker_id, gid)
