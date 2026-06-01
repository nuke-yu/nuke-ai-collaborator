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

import db
from runtime import tracing
from runtime import ipc
from runtime.dbpaths import group_db_path
import permissions
from core import bg

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, worker_id, addr, *, bus=None, dispatch=None, on_abort=None):
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

    async def connect(self) -> None:
        self._reader, self._writer = await ipc.connect(self.addr)
        # Identify ourselves so the Supervisor can route group→worker (CELL-12).
        await ipc.send_msg(self._writer, {"type": ipc.protocol.HELLO, "worker_id": self.worker_id})
        # Register the wildcard subscription synchronously BEFORE we start
        # processing downstream messages, so no early bus event is missed.
        self._sub = self.bus.subscribe_all()
        self._upstream_task = asyncio.create_task(self._pump_upstream())
        self._report_task = asyncio.create_task(self._report_stats_loop())

    async def run(self) -> None:
        tracing.setup_structured_logging(log_file=f"logs/worker-{self.worker_id}.log")
        if self._writer is None:
            await self.connect()
        try:
            while True:
                msg = await ipc.recv_msg(self._reader)
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

        if self._report_task:
            self._report_task.cancel()
        if self._upstream_task:
            self._upstream_task.cancel()
            self._upstream_task = None
        self._report_task = None
        if self._writer:
            self._writer.close()
            self._writer = None

    async def _pump_upstream(self) -> None:
        async with self._sub as sub:
            async for payload in sub:
                gid = payload.get("group_id")
                if gid is None:
                    continue  # un-routable events aren't forwarded to the supervisor
                await ipc.send_msg(self._writer, ipc.protocol.envelope(
                    ipc.protocol.BROADCAST, group_id=gid, payload=payload,
                ))

    
    
    async def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        gid = msg.get("group_id")
        tid = msg.get("trace_id")
        
        with tracing.trace_context(trace_id=tid, group_id=gid):
            if t == ipc.protocol.USER_MESSAGE:
                from runtime.lifecycle import manager as lifecycle
                db_path = await lifecycle.hydrate(gid)
                with db.bind_db(db_path):
                    await self._dispatch(msg)
            elif t == ipc.protocol.ABORT:
                if self._on_abort:
                    self._on_abort(gid)
                else:
                    bg.abort_group(gid)
            elif t == ipc.protocol.PERMISSION_RESPONSE:
                request_id = msg.get("request_id", "")
                approved = bool(msg.get("approved", False))
                persistence = msg.get("persistence", "once")
                req = permissions.resolve(request_id, approved, persistence, group_id=gid)
                if req and approved and persistence == "always":
                    from runtime.lifecycle import manager as lifecycle
                    db_path = await lifecycle.hydrate(gid)
                    with db.bind_db(db_path):
                        bg.spawn(permissions.save_rule(req.bot_id, req.tool_name, "", "allow"))

            elif t == ipc.protocol.WAKE_TRIGGER:
                from runtime.lifecycle import manager as lifecycle
                db_path = await lifecycle.hydrate(gid)
                with db.bind_db(db_path):
                    from runtime.dispatch import dispatch_wake_trigger
                    await dispatch_wake_trigger(msg)
            elif t == ipc.protocol.RELEASE_LEASE:
                # CELL-18: Gracefully close the group and ACK the Supervisor
                from runtime.lifecycle import manager as lifecycle
                await lifecycle.evict(gid)
                await ipc.send_msg(self._writer, ipc.protocol.envelope(
                    ipc.protocol.LEASE_RELEASED, group_id=gid, trace_id=tid
                ))
            else:

                log.debug("worker %s: unhandled downstream type=%s", self.worker_id, t)



    async def _default_dispatch(self, msg: dict) -> None:
        raise NotImplementedError(
            "Worker dispatch not wired — injected by the Supervisor integration (CELL-12)"
        )
