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
        self._processes: list[asyncio.subprocess.Process] = []

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._server = await ipc.serve(self.addr, self._on_worker_conn)
        if self._num_workers > 0:
            await self._spawn_workers(self._num_workers)

    async def _spawn_workers(self, k: int) -> None:
        log.info("supervisor: spawning %d worker processes", k)
        for i in range(k):
            wid = f"w{i}"
            # Use sys.executable to ensure we use the same python interpreter
            # Use -m runtime.entry to start the worker
            cmd = [sys.executable, "-m", "runtime.entry", "--role", "worker", "--id", wid, "--addr", self.addr]
            try:
                proc = await asyncio.create_subprocess_exec(*cmd)
                self._processes.append(proc)
                log.info("supervisor: started worker %s (pid=%d)", wid, proc.pid)
            except Exception:
                log.exception("supervisor: failed to spawn worker %s", wid)



    async def stop(self) -> None:
        # 1. Kill worker processes
        for proc in self._processes:
            try:
                proc.terminate()
            except Exception:
                pass
        
        if self._processes:
            await asyncio.gather(*(proc.wait() for proc in self._processes), return_exceptions=True)
        self._processes.clear()

        # 2. Close IPC connections

        for w in list(self._workers.values()):
            w.close()
        self._workers.clear()
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2)
            except Exception:
                pass

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
            self._workers[wid] = writer
            log.info("supervisor: worker %s connected", wid)
            while True:
                frame = await ipc.recv_msg(reader)
                await self._on_upstream(frame)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if wid is not None and self._workers.get(wid) is writer:
                del self._workers[wid]
                log.info("supervisor: worker %s disconnected", wid)

    
    async def _on_upstream(self, frame: dict) -> None:
        t = frame.get("type")
        gid = frame.get("group_id")
        tid = frame.get("trace_id")
        
        
        with tracing.trace_context(trace_id=tid, group_id=gid):
            if t == ipc.protocol.BROADCAST:
                await self._fanout(gid, frame.get("payload", {}))
            elif t == ipc.protocol.UNREAD_DELTA:
                if self._on_unread:
                    await self._on_unread(gid, frame)
            
            elif t == ipc.protocol.STATS_REPORT:
                payload = frame.get("payload", {})
                wid = payload.get("worker_id")
                if wid:
                    self._worker_stats[wid] = payload
            elif t == ipc.protocol.LEASE_RELEASED:
                fut = self._pending_handoffs.get(gid)
                if fut and not fut.done():
                    fut.set_result(True)
            else:


                log.debug("supervisor: unhandled upstream type=%s", t)


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
            raise RuntimeError(f"no connected worker for group {group_id} (route -> {wid!r})")
        await ipc.send_msg(writer, msg)


    async def _default_route(self, group_id: int) -> str:
        import time
        cached = self._routing_cache.get(group_id)
        if cached:
            wid, expire_at = cached
            if time.time() < expire_at:
                return wid
        
        async with db.global_db() as cdb:
            wid = await queries.get_group_assigned_worker(cdb, group_id)
        
        # M-6: Cache for 60 seconds to allow for external DB updates
        self._routing_cache[group_id] = (wid, time.time() + 60.0)
        return wid
    def get_stats(self) -> dict:
        return {
            "workers": self._worker_stats,
            "active_workers": list(self._workers.keys()),
            "process_count": len(self._processes),
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
        fut = asyncio.get_event_loop().create_future()
        self._pending_handoffs[group_id] = fut
        
        try:
            # Tell old worker to release
            await ipc.send_msg(old_writer, ipc.protocol.envelope(
                ipc.protocol.RELEASE_LEASE, group_id=group_id
            ))
            # Wait for ACK
            await asyncio.wait_for(fut, timeout=10.0)
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
