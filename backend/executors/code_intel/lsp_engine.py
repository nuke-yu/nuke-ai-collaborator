"""JS/TS code intelligence via typescript-language-server (LSP over stdio).

Like jedi, the language server does *static analysis* (it does not execute the
project's code), so it runs in the worker scoped to the project root — same
trust level as jedi_engine, NOT coupled to the Docker sandbox.

Structure:
- StdioLspClient: spawns the server, frames JSON-RPC over stdio, correlates
  responses by id. The only piece that needs a live server.
- LspEngine: implements CodeIntelEngine by orchestrating the LSP handshake +
  request for each operation. Takes a client_factory so tests inject a fake
  client (the real server isn't needed to test the engine's logic).

Per-call spawn (start → initialize → didOpen → request → close) is simple and
correct; a persistent per-root warm server is a perf follow-up.
"""
from __future__ import annotations

import asyncio
import shutil
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


class StdioLspClient:
    """Minimal JSON-RPC-over-stdio LSP client for one server process."""

    def __init__(self, cmd: list[str], cwd: str | None = None):
        self._cmd = cmd
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0

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
            pass

    def _dispatch(self, msg: dict) -> None:
        mid = msg.get("id")
        if mid is not None and mid in self._pending:      # response to our request
            fut = self._pending.pop(mid)
            if not fut.done():
                fut.set_result(msg.get("result"))
        # server-initiated requests / notifications are ignored (we send {} caps)

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
            if self._proc and self._proc.returncode is None:
                await self.notify("exit", {})
        except Exception:
            pass
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
            except Exception:
                pass


def _default_factory(cmd: list[str], cwd: str) -> StdioLspClient:
    return StdioLspClient(cmd, cwd=cwd)


class LspEngine:
    def __init__(self, server_cmd: list[str] | None = None, client_factory=None):
        self._cmd = server_cmd or [config.TS_LANGUAGE_SERVER, "--stdio"]
        self._factory = client_factory or _default_factory

    def available(self) -> bool:
        return shutil.which(self._cmd[0]) is not None

    @staticmethod
    def _language_id(file: Path) -> str:
        return _LANGUAGE_ID.get(file.suffix.lower(), "javascript")

    async def _session(self, file: Path, root: Path):
        """Start a client, run the initialize handshake, didOpen the file.
        Returns the live client; caller must close() it."""
        client = self._factory(self._cmd, str(root))
        await client.start()
        await client.request("initialize", P.initialize_params(str(root)))
        await client.notify("initialized", {})
        text = file.read_text(encoding="utf-8", errors="replace")
        await client.notify(
            "textDocument/didOpen",
            P.did_open_params(str(file), text, self._language_id(file)),
        )
        return client

    async def _run(self, file, root, method, params, parse):
        client = await self._session(file, root)
        try:
            result = await client.request(method, params)
            return parse(result)
        finally:
            await client.close()

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
