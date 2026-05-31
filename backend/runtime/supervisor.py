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
import logging

from runtime import ipc

log = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, addr, *, route=None, on_unread=None):
        self.addr = addr
        self._workers: dict = {}                 # worker_id -> StreamWriter
        self._browsers: dict[int, set] = {}      # group_id -> {client}
        self._route = route or self._default_route
        self._on_unread = on_unread              # async (group_id, payload) -> None
        self._server = None

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._server = await ipc.serve(self.addr, self._on_worker_conn)

    async def stop(self) -> None:
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
        if t == ipc.protocol.BROADCAST:
            await self._fanout(frame.get("group_id"), frame.get("payload", {}))
        elif t == ipc.protocol.UNREAD_DELTA:
            if self._on_unread:
                await self._on_unread(frame.get("group_id"), frame)
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
        for client in list(self._browsers.get(group_id, ())):
            try:
                await client.send(payload)
            except Exception:
                # A dead/slow browser is dropped; real WS impl reuses the DFT-030
                # send-timeout so one bad client can't stall the fan-out.
                self.unregister_browser(group_id, client)

    # ── downstream ───────────────────────────────────────────────────────
    async def send_to_worker(self, group_id: int, msg: dict) -> None:
        wid = self._route(group_id)
        writer = self._workers.get(wid)
        if writer is None:
            raise RuntimeError(f"no connected worker for group {group_id} (route -> {wid!r})")
        await ipc.send_msg(writer, msg)

    def _default_route(self, group_id: int):
        # Placeholder until CELL-15 (persistent assigned_worker_id): pin to the
        # single connected worker in dev; multi-worker needs an explicit route.
        return next(iter(self._workers), None)
