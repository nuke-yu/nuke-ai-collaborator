"""Tests for the code_intel builtin + jedi engine (Phase 1.5).

Real jedi (a hard dependency) drives the engine tests against a two-file temp
project, proving cross-file definition/references actually resolve.
"""
import os
import sys
import asyncio
import tempfile
import unittest
import builtins
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.code_intel.jedi_engine import JediEngine
from executors.code_intel import router
from executors.plugins.code_intel_tool import (
    _handle_code_intel, _project_root, _rel, _format_locations,
)
from executors.code_intel.engine import Location


def _make_project(root: Path):
    # process_order defined in lib.py at line 1, col 4 (0-based) → char 5 (1-based)
    (root / "lib.py").write_text("def process_order(x):\n    return x + 1\n")
    # used in main.py at line 2, col 6 (0-based) → char 7 (1-based)
    (root / "main.py").write_text("from lib import process_order\nprint(process_order(5))\n")


class TestJediEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_project(self.root)
        self.engine = JediEngine()

    def tearDown(self):
        self._td.cleanup()

    async def test_available(self):
        self.assertTrue(self.engine.available())

    def test_available_logs_when_import_fails(self):
        from executors import code_intel as _ci
        old = _ci.jedi_engine._JEDI_AVAILABLE
        _ci.jedi_engine._JEDI_AVAILABLE = None
        engine = JediEngine()
        orig_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "jedi":
                raise ImportError("boom")
            return orig_import(name, *args, **kwargs)

        try:
            with patch("builtins.__import__", side_effect=fake_import), \
                 self.assertLogs("executors.code_intel.jedi_engine", level="ERROR") as logs:
                self.assertFalse(engine.available())
            self.assertTrue(any("availability check failed" in line for line in logs.output))
        finally:
            _ci.jedi_engine._JEDI_AVAILABLE = old

    async def test_definition_cross_file(self):
        # from the usage in main.py → jump to the def in lib.py
        locs = await self.engine.definition(self.root / "main.py", 2, 7, self.root)
        self.assertTrue(any(Path(l.file).name == "lib.py" and l.line == 1 for l in locs))

    async def test_references_cross_file(self):
        # from the def in lib.py → references span lib.py AND main.py
        locs = await self.engine.references(self.root / "lib.py", 1, 5, self.root)
        files = {Path(l.file).name for l in locs}
        self.assertIn("lib.py", files)
        self.assertIn("main.py", files)

    async def test_hover_has_signature(self):
        text = await self.engine.hover(self.root / "main.py", 2, 7, self.root)
        self.assertIn("process_order", text)

    async def test_hover_logs_optional_lookup_failures(self):
        class _Name:
            type = "function"
            name = "demo"
            description = "demo()"
            def get_signatures(self):
                raise RuntimeError("sig failed")
            def docstring(self, raw=True):
                raise RuntimeError("doc failed")

        with patch.object(self.engine, "_script") as mock_script, \
             self.assertLogs("executors.code_intel.jedi_engine", level="ERROR") as logs:
            mock_script.return_value.help.return_value = [_Name()]
            text = await self.engine.hover(self.root / "main.py", 2, 7, self.root)

        self.assertIn("demo", text)
        self.assertTrue(any("signature lookup failed" in line for line in logs.output))
        self.assertTrue(any("docstring lookup failed" in line for line in logs.output))

    async def test_document_symbols(self):
        locs = await self.engine.document_symbols(self.root / "lib.py", self.root)
        self.assertTrue(any("process_order" in l.snippet or l.line == 1 for l in locs))


class TestRouter(unittest.TestCase):
    def test_python_routes_to_engine(self):
        self.assertIsNotNone(router.get_engine(".py"))
        self.assertIsNotNone(router.get_engine(".PY"))   # case-insensitive
        self.assertIsNotNone(router.get_engine(".pyi"))

    def test_unsupported_lang_is_none(self):
        # extensions not in the router at all (.js IS supported now — it routes
        # to the LSP engine; whether it resolves depends on ts-ls availability,
        # which is environment-dependent, so don't assert on it here)
        self.assertIsNone(router.get_engine(".txt"))
        self.assertIsNone(router.get_engine(".go"))
        self.assertIsNone(router.get_engine(""))

    def test_supported_extensions(self):
        self.assertIn(".py", router.supported_extensions())


class TestToolHelpers(unittest.TestCase):
    def test_project_root_finds_marker(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            repo = ws / "workspace" / "repo"
            (repo / "pkg").mkdir(parents=True)
            (repo / "pyproject.toml").write_text("[tool]\n")
            f = repo / "pkg" / "m.py"
            f.write_text("x=1\n")
            self.assertEqual(_project_root(f, ws), repo)

    def test_project_root_falls_back_to_ws_root(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            f = ws / "a.py"
            f.write_text("x=1\n")
            self.assertEqual(_project_root(f, ws), ws)

    def test_rel_and_format(self):
        ws = Path("/ws")
        locs = [Location(file="/ws/a.py", line=3, character=5, snippet="def a")]
        out = _format_locations(locs, ws, header="引用")
        self.assertIn("引用：1 处", out)
        self.assertIn("a.py:3:5  def a", out)

    def test_format_empty(self):
        self.assertEqual(_format_locations([], Path("/ws"), header="定义"), "未找到定义")


class TestHandleCodeIntel(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_project(self.root)
        (self.root / "note.txt").write_text("process_order mentioned\n")

    def tearDown(self):
        self._td.cleanup()

    def _patched(self):
        # map the tool's two _resolve_shell_cwd calls (file → abs, "" → ws root)
        def fake(path, bot_id, group_id):
            if not path:
                return (str(self.root), "")
            return (str(self.root / path), "")
        return patch(
            "executors.plugins.code_intel_tool._resolve_shell_cwd", side_effect=fake
        )

    async def _run(self, **kw):
        with self._patched():
            return await _handle_code_intel(context={"bot_id": 1, "group_id": 1}, **kw)

    async def test_references_cross_file(self):
        out = await self._run(operation="references", file="lib.py", line=1, character=5)
        self.assertIn("lib.py:1", out)
        self.assertIn("main.py", out)

    async def test_definition(self):
        out = await self._run(operation="definition", file="main.py", line=2, character=7)
        self.assertIn("lib.py:1", out)

    async def test_document_symbols_no_position_needed(self):
        out = await self._run(operation="document_symbols", file="lib.py")
        self.assertIn("符号", out)

    async def test_unsupported_language(self):
        out = await self._run(operation="references", file="note.txt", line=1, character=1)
        self.assertIn("暂不支持", out)
        self.assertIn("search", out)

    async def test_bad_operation(self):
        out = await self._run(operation="rename", file="lib.py", line=1, character=5)
        self.assertIn("参数错误", out)

    async def test_position_required(self):
        out = await self._run(operation="references", file="lib.py")
        self.assertIn("需要 line 和 character", out)

    async def test_missing_file(self):
        out = await self._run(operation="definition", file="nope.py", line=1, character=1)
        self.assertIn("文件不存在", out)


if __name__ == "__main__":
    unittest.main()
