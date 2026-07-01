"""CELL-12: Supervisor routing/fan-out engine (Project-Cell Isolation V3).

The Supervisor is the single entry process. This module is its core routing
engine (the FastAPI/WebSocket termination shell is wired at integration time,
CELL-14):

  - IPC server: workers connect in and identify with a HELLO frame; the
    Supervisor tracks worker_id → writer.
  - downstream: send_to_worker(group_id, msg) routes a control/user frame to the
    worker that owns the group (route(group_id) → worker_id), pinned per
    connection at the WS layer.
  - upstream: a worker's BROADCAST frame is fanned out to every browser client
    registered for that group; UNREAD_DELTA is folded into the central
    unread_counts projection (Supervisor is the sole writer, V3 §10.1).

Browser clients are abstracted as objects with `async send(payload)` so this
engine is testable without a real WebSocket. Group→worker assignment is a
pluggable `route`; the persistent assigned_worker_id table is CELL-15.
"""
import asyncio
import sys
import logging

from core import config
from runtime import tracing
from runtime import ipc
import db
from db import queries

log = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, addr, *, route=None, on_unread=None, **kwargs):
        self.addr = addr
        self._workers: dict = {}                 # worker_id -> StreamWriter
        self._browsers: dict[int, set] = {}      # group_id -> {client}
        self._route = route or self._default_route
        self._routing_cache: dict[int, tuple[str, float]] = {}  # group_id -> (worker_id, expire_at)
        self._worker_stats: dict[str, dict] = {} # worker_id -> latest stats
        self._pending_handoffs: dict[int, asyncio.Future] = {} # group_id -> future
        self._on_unread = on_unread              # async (group_id, payload) -> None
        self._server = None
        self._num_workers = kwargs.get("num_workers", 0)
        # DFT-032: carry the child's label alongside the handle so the metrics
        # collector can attribute RSS/CPU/liveness per worker/collector.
        self._processes: list[tuple[str, asyncio.subprocess.Process]] = []
        self._restart_counts: dict[str, int] = {}      # label -> restart count
        self._worker_stats_ts: dict[str, float] = {}   # worker_id -> last STATS_REPORT epoch
        self._stopping = False
        self._monitor_tasks: set[asyncio.Task] = set()
        # Latest MCP tool-schema snapshot pushed by the collector; cached so a
        # worker connecting later (or after a ToolListChanged) gets the current set.
        self._mcp_schemas: dict | None = None

    def _drop_worker_routes(self, worker_id: str) -> None:
        stale_groups = [
            group_id
            for group_id, (cached_worker_id, _expire_at) in self._routing_cache.items()
            if cached_worker_id == worker_id
        ]
        for group_id in stale_groups:
            self._routing_cache.pop(group_id, None)
        return stale_groups

    def _drop_worker_state(self, worker_id: str, writer=None) -> None:
        current = self._workers.get(worker_id)
        if writer is not None and current is not writer:
            return
        self._workers.pop(worker_id, None)
        stale_groups = self._drop_worker_routes(worker_id)
        for group_id in stale_groups:
            fut = self._pending_handoffs.get(group_id)
            if fut and not fut.done():
                fut.set_result(False)
        self._worker_stats.pop(worker_id, None)
        self._worker_stats_ts.pop(worker_id, None)

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        tracing.setup_structured_logging(log_file="logs/supervisor.log")
        self._server = await ipc.serve(self.addr, self._on_worker_conn)
        if self._num_workers > 0:
            await self._spawn_workers(self._num_workers)
            # MCP is a cross-group capability → one collector process, not per worker.
            await self._spawn_collector()

    async def _spawn_collector(self) -> None:
        t = asyncio.create_task(self._run_process_loop(
            "mcp-collector",
            [sys.executable, "-m", "runtime.entry", "--role", "mcp-collector", "--addr", self.addr],
        ))
        self._monitor_tasks.add(t)
        t.add_done_callback(self._monitor_tasks.discard)

    async def _spawn_workers(self, k: int) -> None:
        log.info("supervisor: spawning %d worker processes", k)
        for i in range(k):
            wid = f"w{i}"
            t = asyncio.create_task(self._run_process_loop(
                wid,
                [sys.executable, "-m", "runtime.entry", "--role", "worker", "--id", wid, "--addr", self.addr],
            ))
            self._monitor_tasks.add(t)
            t.add_done_callback(self._monitor_tasks.discard)

    async def _run_process_loop(self, label: str, cmd: list) -> None:
        """Spawn a subprocess and restart it with exponential backoff on crash."""
        # DFT-032: seed the counter to 0 so the series exists before any restart.
        self._restart_counts.setdefault(label, 0)
        backoff = 1.0
        while not self._stopping:
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(*cmd)
                entry = (label, proc)
                self._processes.append(entry)
                log.info("supervisor: started %s (pid=%d)", label, proc.pid)
                await proc.wait()
                self._processes.remove(entry)
                if self._stopping:
                    break
                # DFT-032: count the restart (the loop is about to re-spawn).
                self._restart_counts[label] = self._restart_counts.get(label, 0) + 1
                log.warning("supervisor: %s exited (code=%d), restarting in %.0fs",
                            label, proc.returncode, backoff)
            except asyncio.CancelledError:
                if proc:
                    proc.terminate()
                break
            except Exception:
                log.exception("supervisor: failed to spawn %s", label)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)



    async def stop(self) -> None:
        self._stopping = True

        # 1. Cancel monitor/restart tasks first so they don't re-spawn
        for t in list(self._monitor_tasks):
            t.cancel()
        if self._monitor_tasks:
            await asyncio.gather(*self._monitor_tasks, return_exceptions=True)
        self._monitor_tasks.clear()

        # 2. Kill worker processes
        for _label, proc in self._processes:
            try:
                proc.terminate()
            except Exception:
                pass

        if self._processes:
            await asyncio.gather(*(proc.wait() for _label, proc in self._processes),
                                 return_exceptions=True)
        self._processes.clear()

        # 2. Close IPC connections

        waiters = []
        for w in list(self._workers.values()):
            w.close()
            wait_closed = getattr(w, "wait_closed", None)
            if callable(wait_closed):
                waiters.append(wait_closed())
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)
        self._workers.clear()
        self._worker_stats.clear()
        self._worker_stats_ts.clear()
        self._routing_cache.clear()
        for fut in self._pending_handoffs.values():
            if not fut.done():
                fut.cancel()
        self._pending_handoffs.clear()
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2)
            except Exception:
                pass
            self._server = None

    # ── worker side (IPC) ────────────────────────────────────────────────
    async def _on_worker_conn(self, reader, writer) -> None:
        wid = None
        try:
            hello = await ipc.recv_msg(reader)
            if hello.get("type") != ipc.protocol.HELLO:
                log.warning("supervisor: first frame not HELLO, dropping")
                writer.close()
                return
            wid = hello["worker_id"]
            # H-6: Close old connection if this worker_id is reconnecting
            old_w = self._workers.get(wid)
            if old_w:
                try: old_w.close()
                except Exception: pass
            self._workers[wid] = writer
            log.info("supervisor: worker %s connected", wid)
            # Hand a freshly-connected worker the current MCP schema snapshot so it
            # can advertise MCP tools without a round-trip (collector pushes updates).
            if wid != ipc.protocol.MCP_COLLECTOR_ID and self._mcp_schemas is not None:
                try:
                    await ipc.send_msg(writer, self._mcp_schemas)
                except Exception:
                    self._drop_worker_state(wid, writer=writer)
                    try:
                        writer.close()
                    except Exception:
                        pass
                    log.warning("supervisor: failed to send cached MCP schemas to %s", wid)
                    return
            while True:
                frame = await ipc.recv_msg(reader)
                await self._on_upstream(frame)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if wid is not None:
                self._drop_worker_state(wid, writer=writer)
                log.info("supervisor: worker %s disconnected", wid)

    
    async def _on_upstream(self, frame: dict) -> None:
        t = frame.get("type")
        gid = frame.get("group_id")
        tid = frame.get("trace_id")
        
        try:
            with tracing.trace_context(trace_id=tid, group_id=gid):
                if t == ipc.protocol.BROADCAST:
                    payload = frame.get("payload", {})
                    await self._fanout(gid, payload)
                    # Maintain the central unread projection: a newly-posted chat
                    # message bumps unread for offline members (their 'read' resets it).
                    if (self._on_unread and gid is not None
                            and payload.get("type") in ("message", "stream_end")
                            and isinstance(payload.get("id"), int)):
                        await self._on_unread(gid, payload)
                elif t == ipc.protocol.UNREAD_DELTA:
                    if self._on_unread:
                        await self._on_unread(gid, frame)
                elif t == ipc.protocol.STATS_REPORT:
                    payload = frame.get("payload", {})
                    wid = payload.get("worker_id")
                    if wid:
                        import time
                        self._worker_stats[wid] = payload
                        self._worker_stats_ts[wid] = time.time()  # DFT-032 heartbeat freshness
                elif t == ipc.protocol.LEASE_RELEASED:
                    fut = self._pending_handoffs.get(gid)
                    if fut and not fut.done():
                        fut.set_result(True)
                elif t in (ipc.protocol.MCP_CALL, ipc.protocol.MCP_AUTH_START):
                    # worker → collector. If the collector is down, reply an error
                    # to the origin worker so its awaiting call doesn't hang.
                    if not await self.send_to_worker_id(ipc.protocol.MCP_COLLECTOR_ID, frame):
                        error_result = "[MCP错误] collector 未就绪"
                        if t == ipc.protocol.MCP_AUTH_START:
                            error_result = "[MCP认证错误] collector 未就绪"
                        await self.send_to_worker_id(frame.get("origin_worker_id"),
                            ipc.protocol.envelope(
                                ipc.protocol.MCP_RESULT, group_id=gid, trace_id=tid,
                                request_id=frame.get("request_id"),
                                origin_worker_id=frame.get("origin_worker_id"),
                                result=error_result, is_error=True,
                            ))
                elif t == ipc.protocol.MCP_RESULT:
                    # collector → origin worker
                    await self.send_to_worker_id(frame.get("origin_worker_id"), frame)
                elif t == ipc.protocol.MCP_SCHEMAS:
                    # collector pushed a new snapshot → cache + fan out to all workers
                    self._mcp_schemas = frame
                    for wid, writer in list(self._workers.items()):
                        if wid == ipc.protocol.MCP_COLLECTOR_ID:
                            continue
                        try:
                            await ipc.send_msg(writer, frame)
                        except Exception:
                            self._drop_worker_state(wid, writer=writer)
                            log.warning("supervisor: failed to push MCP schemas to %s", wid)
                else:
                    log.debug("supervisor: unhandled upstream type=%s", t)
        except Exception:
            log.exception("supervisor: error processing upstream frame type=%s", t)


    # ── browser side ─────────────────────────────────────────────────────
    def register_browser(self, group_id: int, client) -> None:
        self._browsers.setdefault(group_id, set()).add(client)

    def unregister_browser(self, group_id: int, client) -> None:
        bucket = self._browsers.get(group_id)
        if bucket:
            bucket.discard(client)
            if not bucket:
                self._browsers.pop(group_id, None)

    
    async def _fanout(self, group_id, payload: dict) -> None:
        # CELL-21 (DFT-030 logic): Defend against head-of-line blocking.
        # If a single WebSocket connection is half-open or TCP window is full,
        # client.send() could hang indefinitely and stall the Supervisor's
        # upstream IPC receiver, delaying broadcasts for ALL healthy clients
        # in ALL groups.
        _SEND_TIMEOUT = config.SUPERVISOR_SEND_TIMEOUT
        dead = []
        clients = list(self._browsers.get(group_id, ()))
        
        for client in clients:
            try:
                await asyncio.wait_for(client.send(payload), _SEND_TIMEOUT)
            except Exception:
                log.warning("supervisor: client send timed out or failed, evicting")
                dead.append(client)
                
        for client in dead:
            self.unregister_browser(group_id, client)
            # H-4: If the client has a close method (WSClientProxy), call it to 
            # trigger presence updates and manager cleanup.
            if hasattr(client, "close"):
                asyncio.create_task(client.close())


    # ── downstream ───────────────────────────────────────────────────────
    async def send_to_worker(self, group_id: int, msg: dict) -> None:
        wid = await self._route(group_id)
        writer = self._workers.get(wid)
        if writer is None:
            self._routing_cache.pop(group_id, None)
            wid = await self._route(group_id)
            writer = self._workers.get(wid)
        if writer is None:
            raise RuntimeError(f"no connected worker for group {group_id} (route -> {wid!r})")
        await ipc.send_msg(writer, msg)

    async def send_to_worker_id(self, worker_id: str, msg: dict) -> bool:
        """Send directly to a connection by worker_id (collector + MCP_RESULT relay,
        which route by worker_id, not by group). Returns False if not connected."""
        writer = self._workers.get(worker_id)
        if writer is None:
            log.warning("supervisor: no connection for worker_id=%s, dropping %s",
                        worker_id, msg.get("type"))
            return False
        try:
            await ipc.send_msg(writer, msg)
            return True
        except Exception:
            self._drop_worker_state(worker_id, writer=writer)
            log.exception("supervisor: send_to_worker_id failed for %s", worker_id)
            return False


    def _default_worker_for(self, group_id: int) -> str:
        """Deterministic, stateless spread for an unassigned group. Same group_id
        always maps to the same worker, so no DB write is needed and it survives
        restarts. num_workers≤0 (unconfigured/single-worker) collapses to w0."""
        n = self._num_workers if self._num_workers and self._num_workers > 0 else 1
        return f"w{group_id % n}"

    async def _default_route(self, group_id: int) -> str:
        import time
        cached = self._routing_cache.get(group_id)
        if cached:
            wid, expire_at = cached
            if time.time() < expire_at:
                return wid

        async with db.global_db() as cdb:
            wid = await queries.get_group_assigned_worker(cdb, group_id)

        # Unassigned (NULL / no row) → spread by modulo instead of piling on w0.
        if not wid:
            wid = self._default_worker_for(group_id)

        # M-6: Cache for 60 seconds to allow for external DB updates
        self._routing_cache[group_id] = (wid, time.time() + 60.0)
        return wid
    def get_stats(self) -> dict:
        return {
            "workers": self._worker_stats,
            "active_workers": list(self._workers.keys()),
            "process_count": len(self._processes),
            "browsers_by_group": {g: len(s) for g, s in self._browsers.items()},
        }

    async def reassign_group(self, group_id: int, new_worker_id: str) -> None:
        """CELL-18: Safely transfer a group to a new worker with clean lease handoff."""
        # 1. Update persistent DB
        async with db.global_db() as cdb:
            await cdb.execute("UPDATE groups SET assigned_worker_id = ? WHERE id = ?", (new_worker_id, group_id))
            await cdb.commit()
            
        # 2. Check who currently owns it in memory
        cached_old = self._routing_cache.get(group_id)
        old_wid = cached_old[0] if cached_old else None
        if not old_wid or old_wid == new_worker_id:
            import time
            self._routing_cache[group_id] = (new_worker_id, time.time() + 3600.0)
            return
            
        old_writer = self._workers.get(old_wid)
        if not old_writer:
            # Old worker is dead, safe to just update cache
            import time
            self._routing_cache[group_id] = (new_worker_id, time.time() + 3600.0)
            return

        # 3. Handoff Protocol
        log.info("supervisor: initiating handoff of group %d from %s to %s", group_id, old_wid, new_worker_id)
        fut = asyncio.get_running_loop().create_future()
        prev = self._pending_handoffs.get(group_id)
        if prev and not prev.done():
            prev.cancel()
        self._pending_handoffs[group_id] = fut
        
        try:
            # Tell old worker to release
            sent = await self.send_to_worker_id(old_wid, ipc.protocol.envelope(
                ipc.protocol.RELEASE_LEASE, group_id=group_id
            ))
            if not sent:
                log.warning("supervisor: failed to notify %s to release group %d, forcing routing update", old_wid, group_id)
                return
            # Wait for ACK
            released = await asyncio.wait_for(fut, timeout=10.0)
            if released is False:
                log.warning("supervisor: worker %s disappeared before confirming release of group %d", old_wid, group_id)
            else:
                log.info("supervisor: handoff of group %d complete", group_id)
        except asyncio.TimeoutError:
            log.error("supervisor: timeout waiting for %s to release group %d, forcing routing update anyway", old_wid, group_id)
        finally:
            self._pending_handoffs.pop(group_id, None)
            # 4. Point traffic to new worker
            import time
            self._routing_cache[group_id] = (new_worker_id, time.time() + 3600.0)

# Global instance used by the WS shell and Scheduler
supervisor: 'Supervisor | None' = None
