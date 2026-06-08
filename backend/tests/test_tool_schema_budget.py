"""Tests for #4: external (MCP) tool-schema budget (bounds prompt growth)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.tool_loop_v1 import (
    _apply_external_schema_budget,
    _build_budget_note,
    _filter_mcp_schemas,
    _MAX_EXTERNAL_TOOL_SCHEMAS,
)


def _schema(name):
    return {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}


class TestFilterMcpSchemas(unittest.TestCase):
    """#9: per-bot MCP tool visibility (allow/block globs on {server}__{tool})."""

    def _names(self, schemas):
        return {s["function"]["name"] for s in schemas}

    def setUp(self):
        self.s = [_schema("github__create_issue"), _schema("github__get_file"),
                  _schema("fs__read"), _schema("fs__write")]

    def test_no_config_passthrough(self):
        self.assertEqual(_filter_mcp_schemas(self.s, None, None), self.s)

    def test_block_server_glob(self):
        out = self._names(_filter_mcp_schemas(self.s, None, ["github__*"]))
        self.assertEqual(out, {"fs__read", "fs__write"})

    def test_block_specific_tool(self):
        out = self._names(_filter_mcp_schemas(self.s, None, ["fs__write"]))
        self.assertNotIn("fs__write", out)
        self.assertIn("fs__read", out)

    def test_allow_whitelist(self):
        out = self._names(_filter_mcp_schemas(self.s, ["github__*"], None))
        self.assertEqual(out, {"github__create_issue", "github__get_file"})

    def test_block_wins_over_allow(self):
        out = self._names(_filter_mcp_schemas(self.s, ["github__*"], ["github__get_file"]))
        self.assertEqual(out, {"github__create_issue"})


class TestExternalSchemaBudget(unittest.TestCase):

    def test_under_budget_unchanged(self):
        s = [_schema(f"t{i}") for i in range(5)]
        kept, deferred = _apply_external_schema_budget(s, max_n=10)
        self.assertEqual(kept, s)
        self.assertEqual(deferred, [])

    def test_over_budget_caps_and_reports_deferred(self):
        s = [_schema(f"t{i}") for i in range(15)]
        kept, deferred = _apply_external_schema_budget(s, max_n=10)
        self.assertEqual(len(kept), 10)
        self.assertEqual(deferred, [f"t{i}" for i in range(10, 15)])

    def test_exactly_at_budget_unchanged(self):
        s = [_schema(f"t{i}") for i in range(10)]
        kept, deferred = _apply_external_schema_budget(s, max_n=10)
        self.assertEqual(len(kept), 10)
        self.assertEqual(deferred, [])

    def test_default_budget_value(self):
        self.assertGreater(_MAX_EXTERNAL_TOOL_SCHEMAS, 0)
        s = [_schema(f"t{i}") for i in range(_MAX_EXTERNAL_TOOL_SCHEMAS + 3)]
        kept, deferred = _apply_external_schema_budget(s)
        self.assertEqual(len(kept), _MAX_EXTERNAL_TOOL_SCHEMAS)
        self.assertEqual(len(deferred), 3)

    def test_budget_note_lists_names_and_truncates(self):
        note = _build_budget_note([f"t{i}" for i in range(40)])
        self.assertIn("40", note)
        self.assertIn("allow_list", note)
        self.assertIn("…", note)   # >30 names → truncated display


if __name__ == "__main__":
    unittest.main()
