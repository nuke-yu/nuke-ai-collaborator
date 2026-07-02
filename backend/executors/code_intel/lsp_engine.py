"""JS/TS code intelligence via typescript-language-server (LSP over stdio).

Like jedi, the language server does *static analysis* (it does not execute the
project's code), so it runs in the worker scoped to the project root — same
trust level as jedi_engine, NOT coupled to the Docker sandbox.

Warm-server design (lazy + reused, mirrors OpenCode's lazy-spawn-by-root):
- one persistent StdioLspClient per project root, initialized once and reused
  across calls (the initialize handshake + server warm-up is the expensive bit);
- each query first syncs the target file (didOpen on first touch, else didChange
  with the current on-disk text, so the server sees the bot's latest edits);
- idle roots are evicted by a reaper; a crashed server is respawned on next use.
- StdioLspClient is the only piece needing a live server; LspEngine takes a
  client_factory so tests inject a fake.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from core import config
from executors.code_intel import lsp_protocol as P
from executors.code_intel.engine import Location

_LANGUAGE_ID = {
    ".ts": "typescript", ".tsx": "typescriptreact",
    ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascriptreact",
    ".mjs": "javascript", ".cjs": "javascript",
}

_REQUEST_TIMEOUT_S = 30

log = logging.getLogger(__name__)


class StdioLspClient:
    """Minimal JSON-RPC-over-stdio LSP client for one server process."""

    def __init__(self, cmd: list[str], cwd: str | None = None):
        self._cmd = cmd
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0

    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self._cwd,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        buffer = b""
        assert self._proc and self._proc.stdout
        try:
            while True:
                chunk = await self._proc.stdout.read(65536)
                if not chunk:
                    break
                buffer += chunk
                messages, buffer = P.iter_frames(buffer)
                for msg in messages:
                    self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("lsp engine: read loop failed")
        finally:
            # server gone → don't let in-flight requests hang until timeout
            self._fail_pending(ConnectionError("lsp server closed"))

    def _dispatch(self, msg: dict) -> None:
        mid = msg.get("id")
        if mid is not None and mid in self._pending:      # response to our request
            fut = self._pending.pop(mid)
            if not fut.done():
                fut.set_result(msg.get("result"))
        # server-initiated requests / notifications ignored (we send {} caps)

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    def _send(self, message: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(P.encode_frame(message))

    async def request(self, method: str, params: dict, timeout: float = _REQUEST_TIMEOUT_S):
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        self._send(P.request(rid, method, params))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict) -> None:
        self._send(P.notification(method, params))

    async def close(self) -> None:
        try:
            if self.alive():
                await self.notify("exit", {})
        except Exception:
            log.exception("lsp engine: failed to send exit notification")
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("lsp engine: reader task did not shut down cleanly")
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
            except Exception:
                log.exception("lsp engine: failed to kill lsp process during close")


def _default_factory(cmd: list[str], cwd: str) -> StdioLspClient:
    return StdioLspClient(cmd, cwd=cwd)


class _Session:
    """A warm server for one project root."""
    def __init__(self, client):
        self.client = client
        self.initialized = False
        self.init_lock = asyncio.Lock()
        self.versions: dict[str, int] = {}     # file → last version synced
        self.last_used = time.monotonic()


class LspEngine:
    def __init__(self, server_cmd: list[str] | None = None, client_factory=None):
        self._cmd = server_cmd or [config.TS_LANGUAGE_SERVER, "--stdio"]
        self._factory = client_factory or _default_factory
        self._sessions: dict[str, _Session] = {}
        self._pool_lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None

    def available(self) -> bool:
        return shutil.which(self._cmd[0]) is not None

    @staticmethod
    def _language_id(file: Path) -> str:
        return _LANGUAGE_ID.get(file.suffix.lower(), "javascript")

    # --- warm session pool --------------------------------------------------

    async def _get_session(self, root: str) -> _Session:
        async with self._pool_lock:
            sess = self._sessions.get(root)
            if sess is not None and sess.initialized and not sess.client.alive():
                sess = None                        # server crashed → respawn
            if sess is None:
                sess = _Session(self._factory(self._cmd, root))
                self._sessions[root] = sess
        async with sess.init_lock:                 # one initialize per session
            if not sess.initialized:
                await sess.client.start()
                await sess.client.request("initialize", P.initialize_params(root))
                await sess.client.notify("initialized", {})
                sess.initialized = True
        sess.last_used = time.monotonic()
        self._start_reaper()
        return sess

    async def _sync_doc(self, sess: _Session, file: Path) -> None:
        text = file.read_text(encoding="utf-8", errors="replace")
        fstr = str(file)
        if fstr not in sess.versions:
            sess.versions[fstr] = 1
            await sess.client.notify(
                "textDocument/didOpen",
                P.did_open_params(fstr, text, self._language_id(file)),
            )
        else:
            sess.versions[fstr] += 1               # re-sync latest on-disk edits
            await sess.client.notify(
                "textDocument/didChange",
                P.did_change_params(fstr, text, sess.versions[fstr]),
            )

    async def _run(self, file, root, method, params, parse):
        sess = await self._get_session(str(root))
        await self._sync_doc(sess, Path(file))
        sess.last_used = time.monotonic()
        result = await sess.client.request(method, params)
        return parse(result)

    # --- idle eviction ------------------------------------------------------

    def idle_roots(self, now: float, idle_timeout: float | None = None) -> list[str]:
        idle_timeout = config.LSP_IDLE_TIMEOUT_S if idle_timeout is None else idle_timeout
        return [r for r, s in self._sessions.items() if now - s.last_used > idle_timeout]

    async def reap_once(self, now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        roots = self.idle_roots(now)
        for root in roots:
            sess = self._sessions.pop(root, None)
            if sess:
                await sess.client.close()
        return roots

    def _start_reaper(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def _reaper_loop(self) -> None:
        tick = max(60, min(int(config.LSP_IDLE_TIMEOUT_S), 300))
        while True:
            await asyncio.sleep(tick)
            try:
                await self.reap_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("lsp engine: idle reap failed")

    async def close(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("lsp engine: reaper task did not shut down cleanly")
            self._reaper_task = None
        for sess in list(self._sessions.values()):
            try:
                await sess.client.close()
            except Exception:
                log.exception("lsp engine: failed to close session client")
        self._sessions.clear()

    # --- CodeIntelEngine ops ------------------------------------------------

    async def definition(self, file, line, character, root) -> list[Location]:
        return await self._run(
            file, root, "textDocument/definition",
            P.position_params(str(file), line, character), P.parse_locations,
        )

    async def references(self, file, line, character, root) -> list[Location]:
        return await self._run(
            file, root, "textDocument/references",
            P.references_params(str(file), line, character), P.parse_locations,
        )

    async def hover(self, file, line, character, root) -> str:
        return await self._run(
            file, root, "textDocument/hover",
            P.position_params(str(file), line, character), P.parse_hover,
        )

    async def document_symbols(self, file, root) -> list[Location]:
        return await self._run(
            file, root, "textDocument/documentSymbol",
            P.document_symbol_params(str(file)),
            lambda r: P.parse_document_symbols(r, str(file)),
        )
