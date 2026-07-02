"""Tests for the JS/TS LSP code-intel layer (Phase 2).

The pure protocol layer is tested directly; LspEngine logic is tested with an
injected fake client (a real typescript-language-server isn't needed).
"""
import os
import sys
import time
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.code_intel import lsp_protocol as P
from executors.code_intel.lsp_engine import LspEngine, StdioLspClient
from executors.code_intel import router


# --- pure protocol ----------------------------------------------------------

class TestFraming(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "x", "params": {"a": 1}}
        frame = P.encode_frame(msg)
        self.assertTrue(frame.startswith(b"Content-Length: "))
        msgs, rest = P.iter_frames(frame)
        self.assertEqual(msgs, [msg])
        self.assertEqual(rest, b"")

    def test_two_frames_and_partial_tail(self):
        a = P.encode_frame({"id": 1})
        b = P.encode_frame({"id": 2})
        # feed both frames plus a partial third
        partial = b[:5]
        msgs, rest = P.iter_frames(a + b + partial)
        self.assertEqual([m["id"] for m in msgs], [1, 2])
        self.assertEqual(rest, partial)            # partial preserved for next read

    def test_incomplete_body_waits(self):
        frame = P.encode_frame({"hello": "world"})
        msgs, rest = P.iter_frames(frame[:-3])     # body truncated
        self.assertEqual(msgs, [])
        self.assertEqual(rest, frame[:-3])


class TestUriAndPosition(unittest.TestCase):
    def test_uri_roundtrip(self):
        self.assertEqual(P.uri_to_path(P.path_to_uri("/proj/a b.ts")), "/proj/a b.ts")

    def test_position_params_1based_to_0based(self):
        params = P.position_params("/x.ts", line=2, character=7)
        self.assertEqual(params["position"], {"line": 1, "character": 6})

    def test_references_params_include_declaration(self):
        params = P.references_params("/x.ts", 1, 1)
        self.assertTrue(params["context"]["includeDeclaration"])


class TestParsers(unittest.TestCase):
    def test_parse_locations_plain_and_link(self):
        plain = [{"uri": "file:///proj/lib.ts",
                  "range": {"start": {"line": 0, "character": 4}}}]
        locs = P.parse_locations(plain)
        self.assertEqual((locs[0].file, locs[0].line, locs[0].character),
                         ("/proj/lib.ts", 1, 5))      # 0-based → 1-based
        link = [{"targetUri": "file:///proj/lib.ts",
                 "targetSelectionRange": {"start": {"line": 2, "character": 0}}}]
        self.assertEqual(P.parse_locations(link)[0].line, 3)
        self.assertEqual(P.parse_locations(None), [])

    def test_parse_hover_variants(self):
        self.assertEqual(P.parse_hover({"contents": "hi"}), "hi")
        self.assertEqual(P.parse_hover({"contents": {"value": "sig"}}), "sig")
        self.assertEqual(P.parse_hover({"contents": ["a", {"value": "b"}]}), "a\nb")
        self.assertEqual(P.parse_hover(None), "")

    def test_parse_document_symbols(self):
        syms = [{"name": "foo", "selectionRange": {"start": {"line": 0, "character": 9}},
                 "children": [{"name": "bar", "selectionRange": {"start": {"line": 5, "character": 2}}}]}]
        out = P.parse_document_symbols(syms, "/proj/a.ts")
        names = {l.snippet for l in out}
        self.assertEqual(names, {"foo", "bar"})


# --- engine with a fake client ---------------------------------------------

class _FakeClient:
    def __init__(self, cmd, cwd, responses):
        self.cmd, self.cwd, self.responses = cmd, cwd, responses
        self.requests, self.notifications = [], []
        self.started = self.closed = False

    def alive(self):
        return self.started and not self.closed

    async def start(self):
        self.started = True

    async def request(self, method, params, timeout=30):
        self.requests.append((method, params))
        return self.responses.get(method)

    async def notify(self, method, params):
        self.notifications.append((method, params))

    async def close(self):
        self.closed = True


class TestLspEngine(unittest.IsolatedAsyncioTestCase):
    def _engine(self, responses):
        created = {"clients": []}

        def factory(cmd, cwd):
            c = _FakeClient(cmd, cwd, responses)
            created["clients"].append(c)
            created["client"] = c
            return c

        return LspEngine(server_cmd=["fake-ls", "--stdio"], client_factory=factory), created

    async def _file(self, suffix=".ts"):
        d = tempfile.mkdtemp()
        f = Path(d) / ("a" + suffix)
        f.write_text("export function foo() { return foo(); }\n")
        return f, Path(d)

    async def test_available_checks_binary(self):
        eng = LspEngine(server_cmd=["typescript-language-server", "--stdio"])
        with patch("executors.code_intel.lsp_engine.shutil.which", return_value="/usr/bin/x"):
            self.assertTrue(eng.available())
        with patch("executors.code_intel.lsp_engine.shutil.which", return_value=None):
            self.assertFalse(eng.available())

    async def test_definition_handshake_and_parse(self):
        resp = {
            "initialize": {"capabilities": {}},
            "textDocument/definition": [
                {"uri": "file:///proj/lib.ts",
                 "range": {"start": {"line": 0, "character": 4}}}
            ],
        }
        eng, created = self._engine(resp)
        f, root = await self._file()
        locs = await eng.definition(f, line=2, character=7, root=root)
        self.assertEqual((locs[0].line, locs[0].character), (1, 5))
        client = created["client"]
        self.assertIn("initialize", [m for m, _ in client.requests])
        notes = [m for m, _ in client.notifications]
        self.assertIn("initialized", notes)
        self.assertIn("textDocument/didOpen", notes)
        # position was converted 1-based → 0-based
        defreq = next(p for m, p in client.requests if m == "textDocument/definition")
        self.assertEqual(defreq["position"], {"line": 1, "character": 6})
        # warm: NOT closed after a single call
        self.assertFalse(client.closed)
        await eng.close()

    async def test_session_reused_initialize_once_then_didchange(self):
        resp = {"textDocument/definition": [
            {"uri": "file:///proj/a.ts", "range": {"start": {"line": 0, "character": 0}}}]}
        eng, created = self._engine(resp)
        f, root = await self._file()
        await eng.definition(f, 1, 1, root)
        await eng.definition(f, 2, 1, root)          # same file+root → reuse
        client = created["client"]
        self.assertEqual(len(created["clients"]), 1)  # one server total
        self.assertEqual([m for m, _ in client.requests].count("initialize"), 1)
        notes = [m for m, _ in client.notifications]
        self.assertEqual(notes.count("textDocument/didOpen"), 1)     # opened once
        self.assertGreaterEqual(notes.count("textDocument/didChange"), 1)  # re-synced
        await eng.close()
        self.assertTrue(client.closed)

    async def test_references(self):
        resp = {"textDocument/references": [
            {"uri": "file:///proj/a.ts", "range": {"start": {"line": 3, "character": 0}}},
            {"uri": "file:///proj/b.ts", "range": {"start": {"line": 9, "character": 2}}},
        ]}
        eng, _ = self._engine(resp)
        f, root = await self._file()
        locs = await eng.references(f, 1, 1, root)
        self.assertEqual({Path(l.file).name for l in locs}, {"a.ts", "b.ts"})
        await eng.close()

    async def test_hover(self):
        eng, _ = self._engine({"textDocument/hover": {"contents": {"value": "foo(): void"}}})
        f, root = await self._file()
        self.assertEqual(await eng.hover(f, 1, 1, root), "foo(): void")
        await eng.close()

    async def test_idle_reap_closes_session(self):
        eng, created = self._engine({"textDocument/hover": {"contents": "x"}})
        f, root = await self._file()
        await eng.hover(f, 1, 1, root)
        eng._sessions[str(root)].last_used = time.monotonic() - 99999   # force idle
        reaped = await eng.reap_once()
        self.assertEqual(reaped, [str(root)])
        self.assertTrue(created["client"].closed)
        self.assertNotIn(str(root), eng._sessions)
        await eng.close()

    async def test_crashed_server_respawns(self):
        eng, created = self._engine({"textDocument/hover": {"contents": "x"}})
        f, root = await self._file()
        await eng.hover(f, 1, 1, root)
        created["clients"][0].closed = True          # simulate crash → alive() False
        await eng.hover(f, 1, 1, root)
        self.assertEqual(len(created["clients"]), 2)  # respawned a fresh server
        await eng.close()

    async def test_stdio_client_close_logs_failures(self):
        client = StdioLspClient(["fake-ls", "--stdio"], cwd="/tmp")

        class _Proc:
            def __init__(self):
                self.returncode = None

            def kill(self):
                raise RuntimeError("kill failed")

        async def _reader():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                raise

        client._proc = _Proc()
        client._reader_task = asyncio.create_task(_reader())
        await asyncio.sleep(0)

        async def fake_notify(*_args, **_kwargs):
            raise RuntimeError("exit failed")

        client.notify = fake_notify

        with self.assertLogs("executors.code_intel.lsp_engine", level="ERROR") as logs:
            await client.close()

        self.assertTrue(any("failed to send exit notification" in line for line in logs.output))
        self.assertTrue(any("failed to kill lsp process during close" in line for line in logs.output))


# --- router routing ---------------------------------------------------------

class TestRouterJs(unittest.TestCase):
    def test_js_ts_map_to_lsp_engine(self):
        from executors.code_intel.lsp_engine import LspEngine as _LE
        for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            self.assertIsInstance(router._ENGINE_BY_EXT[ext], _LE)

    def test_get_engine_gates_on_availability(self):
        # patch the JS engine available() True/False and verify gating
        eng = router._ENGINE_BY_EXT[".ts"]
        with patch.object(eng, "available", return_value=True):
            self.assertIs(router.get_engine(".ts"), eng)
        with patch.object(eng, "available", return_value=False):
            self.assertIsNone(router.get_engine(".ts"))


if __name__ == "__main__":
    unittest.main()
