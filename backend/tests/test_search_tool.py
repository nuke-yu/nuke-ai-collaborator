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
        self.assertEqual(out[0], {"path": "src/a.py", "line": 2, "text": "x = foo()"})
        # leading "./" is cleaned (mirror OpenCode clean())
        self.assertEqual(out[1], {"path": "src/a.py", "line": 9, "text": "foo_again"})


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
                {"path": "old.py", "line": 1, "text": "x"},
                {"path": "new.py", "line": 1, "text": "y"},
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
            matches = [{"path": "f.py", "line": i, "text": "z"} for i in range(_RESULT_LIMIT + 25)]
            out = _format_matches(matches, root)
            self.assertIn(f"Found {_RESULT_LIMIT + 25} matches (showing first {_RESULT_LIMIT})", out)
            self.assertIn(f"showing {_RESULT_LIMIT} of {_RESULT_LIMIT + 25} matches (25 hidden)", out)

    def test_long_line_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "f.py").write_text("z\n")
            long_text = "a" * (_MAX_LINE_LENGTH + 500)
            out = _format_matches([{"path": "f.py", "line": 1, "text": long_text}], root)
            self.assertIn("a" * 50, out)
            self.assertIn("...", out)
            self.assertNotIn("a" * (_MAX_LINE_LENGTH + 1), out)


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


if __name__ == "__main__":
    unittest.main()
