"""Tests for the `search` builtin — faithful port of OpenCode's grep tool.

Validates the OpenCode-aligned behavior:
- rg flags (--no-config --json --hidden --glob=!.git/* --no-messages)
- rg --json stream parsing
- group-by-file output, mtime sort, 100-cap, 2000-char line truncation
- real ripgrep integration
"""
import os
import sys
import shutil
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.search_tool import (
    _search_argv, _parse_rg_json, _format_matches, _run_search,
    _RESULT_LIMIT, _MAX_LINE_LENGTH,
)


class TestSearchArgv(unittest.TestCase):
    """rg argv must mirror OpenCode searchArgs()."""

    def test_base_flags(self):
        argv = _search_argv("foo", None, ["."])
        self.assertEqual(
            argv,
            ["rg", "--no-config", "--json", "--hidden", "--glob=!.git/*",
             "--no-messages", "--", "foo", "."],
        )

    def test_include_maps_to_glob(self):
        argv = _search_argv("foo", "*.py", ["."])
        self.assertIn("--glob=*.py", argv)
        # glob comes before the `--` terminator
        self.assertLess(argv.index("--glob=*.py"), argv.index("--"))

    def test_pattern_after_double_dash_is_literal(self):
        # a pattern starting with '-' must not be parsed as a flag
        argv = _search_argv("-foo", None, ["src/a.py"])
        self.assertEqual(argv[-3:], ["--", "-foo", "src/a.py"])

    def test_modifiers_map_to_rg_flags(self):
        argv = _search_argv("foo", None, ["."], case_insensitive=True,
                            whole_word=True, literal=True, context_lines=2)
        self.assertIn("--ignore-case", argv)
        self.assertIn("--word-regexp", argv)
        self.assertIn("--fixed-strings", argv)
        self.assertIn("--context=2", argv)

    def test_no_modifiers_by_default(self):
        argv = _search_argv("foo", None, ["."])
        for flag in ("--ignore-case", "--word-regexp", "--fixed-strings"):
            self.assertNotIn(flag, argv)
        self.assertFalse(any(a.startswith("--context") for a in argv))


class TestParseRgJson(unittest.TestCase):
    def test_parses_match_events_only(self):
        stream = "\n".join([
            '{"type":"begin","data":{"path":{"text":"src/a.py"}}}',
            '{"type":"match","data":{"path":{"text":"src/a.py"},"lines":{"text":"x = foo()\\n"},"line_number":2,"submatches":[]}}',
            '{"type":"match","data":{"path":{"text":"./src/a.py"},"lines":{"text":"foo_again\\n"},"line_number":9,"submatches":[]}}',
            '{"type":"end","data":{"path":{"text":"src/a.py"}}}',
            '{"type":"summary","data":{}}',
            'not json at all',
            '',
        ])
        out = _parse_rg_json(stream)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {"path": "src/a.py", "line": 2, "text": "x = foo()", "match": True})
        # leading "./" is cleaned (mirror OpenCode clean())
        self.assertEqual(out[1], {"path": "src/a.py", "line": 9, "text": "foo_again", "match": True})

    def test_parses_context_events(self):
        stream = "\n".join([
            '{"type":"context","data":{"path":{"text":"a.py"},"lines":{"text":"before\\n"},"line_number":1}}',
            '{"type":"match","data":{"path":{"text":"a.py"},"lines":{"text":"hit\\n"},"line_number":2}}',
            '{"type":"context","data":{"path":{"text":"a.py"},"lines":{"text":"after\\n"},"line_number":3}}',
        ])
        out = _parse_rg_json(stream)
        self.assertEqual([(it["line"], it["match"]) for it in out], [(1, False), (2, True), (3, False)])


class TestFormatMatches(unittest.TestCase):
    def test_empty_is_no_files_found(self):
        self.assertEqual(_format_matches([], Path(".")), "No files found")

    def test_groups_by_file_and_sorts_by_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "old.py").write_text("x\n")
            (root / "new.py").write_text("y\n")
            # make new.py more recently modified than old.py
            os.utime(root / "old.py", (1_000_000, 1_000_000))
            os.utime(root / "new.py", (2_000_000, 2_000_000))
            matches = [
                {"path": "old.py", "line": 1, "text": "x", "match": True},
                {"path": "new.py", "line": 1, "text": "y", "match": True},
            ]
            out = _format_matches(matches, root)
            self.assertTrue(out.startswith("Found 2 matches\n"))
            # newer file group appears before older
            self.assertLess(out.index("new.py:"), out.index("old.py:"))
            self.assertIn("  Line 1: y", out)

    def test_truncates_to_limit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "f.py").write_text("z\n")
            matches = [{"path": "f.py", "line": i, "text": "z", "match": True}
                       for i in range(_RESULT_LIMIT + 25)]
            out = _format_matches(matches, root)
            self.assertIn(f"Found {_RESULT_LIMIT + 25} matches (showing first {_RESULT_LIMIT})", out)
            self.assertIn(f"showing {_RESULT_LIMIT} of {_RESULT_LIMIT + 25} matches (25 hidden)", out)

    def test_long_line_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "f.py").write_text("z\n")
            long_text = "a" * (_MAX_LINE_LENGTH + 500)
            out = _format_matches([{"path": "f.py", "line": 1, "text": long_text, "match": True}], root)
            self.assertIn("a" * 50, out)
            self.assertIn("...", out)
            self.assertNotIn("a" * (_MAX_LINE_LENGTH + 1), out)

    def test_context_lines_rendered_without_label(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "f.py").write_text("a\n")
            items = [
                {"path": "f.py", "line": 1, "text": "before", "match": False},
                {"path": "f.py", "line": 2, "text": "hit", "match": True},
                {"path": "f.py", "line": 3, "text": "after", "match": False},
            ]
            out = _format_matches(items, root)
            self.assertIn("Found 1 matches", out)        # context not counted as match
            self.assertIn("  Line 2: hit", out)          # match line labelled
            self.assertIn("  1: before", out)            # context line, no 'Line'
            self.assertIn("  3: after", out)


@unittest.skipIf(shutil.which("rg") is None, "ripgrep not installed")
class TestRunSearchIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_finds_match(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text("def process_order():\n    return 1\n")
            (root / "b.txt").write_text("nothing here\n")
            out = await _run_search("process_order", root, None)
            self.assertIn("Found 1 matches", out)
            self.assertIn("a.py:", out)
            self.assertIn("Line 1:", out)

    async def test_no_match(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text("hello\n")
            out = await _run_search("zzz_nomatch_zzz", root, None)
            self.assertEqual(out, "No files found")

    async def test_include_filter(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text("target\n")
            (root / "a.js").write_text("target\n")
            out = await _run_search("target", root, "*.py")
            self.assertIn("a.py:", out)
            self.assertNotIn("a.js:", out)

    async def test_literal_matches_regex_special_chars(self):
        # `list.size(` is invalid regex (unbalanced paren) → regex mode errors;
        # literal=True must find it.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.js").write_text("const n = list.size();\n")
            regex_out = await _run_search("list.size(", root, None)
            self.assertIn("[搜索错误]", regex_out)            # unbalanced ( → rg error
            literal_out = await _run_search("list.size(", root, None, literal=True)
            self.assertIn("a.js:", literal_out)
            self.assertIn("Line 1:", literal_out)

    async def test_context_lines(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text("def f():\n    if cond:\n        return False\n")
            out = await _run_search("return False", root, None, context_lines=1)
            self.assertIn("  Line 3: ", out)                 # the match
            self.assertIn("if cond", out)                    # context line above

    async def test_timeout_logs_if_timed_out_process_cannot_be_killed(self):
        class _Proc:
            def __init__(self):
                self.returncode = None

            async def communicate(self):
                await asyncio.sleep(0)
                raise asyncio.TimeoutError

            def kill(self):
                raise RuntimeError("kill failed")

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            return _Proc()

        async def fake_wait_for(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError

        with patch("executors.plugins.search_tool.asyncio.create_subprocess_exec",
                   new=fake_create_subprocess_exec), \
             patch("executors.plugins.search_tool.asyncio.wait_for", new=fake_wait_for), \
             self.assertLogs("executors.plugins.search_tool", level="ERROR") as logs:
            out = await _run_search("foo", Path("."), None)

        self.assertIn("[搜索超时]", out)
        self.assertTrue(any("failed to kill timed-out rg process" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
