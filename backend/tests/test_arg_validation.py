"""Tests for #8: lightweight argument validation against the tool JSON schema."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors import tool_executor as te
from executors.base import ToolDef


def _run(coro):
    return asyncio.run(coro)


_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "count": {"type": "integer"},
        "flag": {"type": "boolean"},
    },
    "required": ["path"],
}


class TestArgValidation(unittest.TestCase):

    def setUp(self):
        self.calls = []
        async def handler(path=None, count=None, flag=None, **_):
            self.calls.append((path, count, flag))
            return "ok"
        te.register(ToolDef(name="vtool", description="", parameters=_SCHEMA), handler)

    def tearDown(self):
        te._registry.clear()
        te.clear_before_hooks()
        te.clear_after_hooks()

    def test_valid_args_run_handler(self):
        result, is_error = _run(te.execute("vtool", {"path": "/x", "count": 3, "flag": True}))
        self.assertFalse(is_error)
        self.assertEqual(result, "ok")
        self.assertEqual(self.calls, [("/x", 3, True)])

    def test_missing_required_blocked(self):
        result, is_error = _run(te.execute("vtool", {"count": 1}))
        self.assertTrue(is_error)
        self.assertIn("缺少必填参数 'path'", result)
        self.assertEqual(self.calls, [])           # handler NOT run

    def test_wrong_type_blocked(self):
        result, is_error = _run(te.execute("vtool", {"path": "/x", "count": "three"}))
        self.assertTrue(is_error)
        self.assertIn("count", result)
        self.assertIn("integer", result)
        self.assertEqual(self.calls, [])

    def test_bool_not_accepted_as_integer(self):
        result, is_error = _run(te.execute("vtool", {"path": "/x", "count": True}))
        self.assertTrue(is_error)
        self.assertIn("boolean", result)

    def test_extra_arg_allowed(self):
        # lenient: unknown args are not rejected (no additionalProperties check)
        result, is_error = _run(te.execute("vtool", {"path": "/x", "extra": "y"}))
        self.assertFalse(is_error)

    def test_none_value_skips_type_check(self):
        result, is_error = _run(te.execute("vtool", {"path": "/x", "count": None}))
        self.assertFalse(is_error)

    def test_empty_schema_no_validation(self):
        async def h(**_): return "done"
        te.register(ToolDef(name="noschema", description="", parameters={}), h)
        result, is_error = _run(te.execute("noschema", {"anything": 1}))
        self.assertFalse(is_error)
        self.assertEqual(result, "done")

    def test_number_accepts_int_and_float(self):
        from executors.tool_executor import _validate_arguments
        sch = {"type": "object", "properties": {"x": {"type": "number"}}}
        self.assertIsNone(_validate_arguments("t", {"x": 3}, sch))
        self.assertIsNone(_validate_arguments("t", {"x": 3.5}, sch))


if __name__ == "__main__":
    unittest.main()
