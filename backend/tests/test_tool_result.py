from __future__ import annotations

import unittest

from executors import tool_executor
from executors.base import ToolDef, ToolResult


class ToolResultTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.registry = dict(tool_executor._registry)
        tool_executor._registry.clear()

    async def asyncTearDown(self) -> None:
        tool_executor._registry.clear()
        tool_executor._registry.update(self.registry)

    async def test_user_text_with_error_prefix_is_not_inferred_as_failure(self) -> None:
        async def read_log(**_kwargs):
            return "[error] this is normal log content"

        tool_executor.register(ToolDef("read_log", "test", {}), read_log)
        result, is_error = await tool_executor.execute("read_log", {}, {})
        self.assertEqual(result, "[error] this is normal log content")
        self.assertFalse(is_error)

    async def test_explicit_tool_result_controls_error_state(self) -> None:
        async def rejected(**_kwargs):
            return ToolResult.error("[错误] denied")

        tool_executor.register(ToolDef("rejected", "test", {}), rejected)
        result, is_error = await tool_executor.execute("rejected", {}, {})
        self.assertEqual(result, "[错误] denied")
        self.assertTrue(is_error)


if __name__ == "__main__":
    unittest.main()
