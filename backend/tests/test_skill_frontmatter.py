"""Tests for fork skill frontmatter handling in tool_loop_v1."""
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------

class TestRunForkSkillWithToolSchemas(unittest.IsolatedAsyncioTestCase):

    async def test_fork_no_schemas_passes_none(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        
        mock_ai = MagicMock()
        mock_ai.call = AsyncMock(return_value={"type": "text", "content": "ok", "usage": {}})
        
        result = await _run_fork_skill("sp", "task", "claude", "claude-opus-4-7", 0.7, mock_ai)
        
        self.assertEqual(result, "ok")
        # Verify call used tools=None
        self.assertIsNone(mock_ai.call.call_args[1].get("tools"))

    async def test_fork_with_schemas_passes_them(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        
        mock_ai = MagicMock()
        mock_ai.call = AsyncMock(return_value={"type": "text", "content": "done", "usage": {}})
        schemas = [{"name": "run_shell", "description": "run shell"}]
        
        result = await _run_fork_skill("sp", "task", "claude", "claude-opus-4-7", 0.7,
                                       mock_ai, tool_schemas=schemas)

        self.assertEqual(result, "done")
        self.assertEqual(mock_ai.call.call_args[1].get("tools"), schemas)

    async def test_fork_tool_calls_result_returns_summary(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        
        mock_ai = MagicMock()
        mock_ai.call = AsyncMock(return_value={
            "type": "tool_calls",
            "calls": [{"name": "run_shell"}],
            "assistant_message": {},
        })

        result = await _run_fork_skill("sp", "task", "claude", "claude-opus-4-7", 0.7,
                                       mock_ai, tool_schemas=[{"name": "run_shell"}])

        self.assertIn("run_shell", result)
        self.assertIn("fork", result)


class TestModelWindowSuffix(unittest.TestCase):
    def test_strips_1m_suffix(self):
        from skills.metadata import strip_context_window_suffix
        self.assertEqual(strip_context_window_suffix("claude-opus-4-8[1m]"), "claude-opus-4-8")
        self.assertEqual(strip_context_window_suffix("claude-opus-4-8 [1m]"), "claude-opus-4-8")

    def test_leaves_plain_model_untouched(self):
        from skills.metadata import strip_context_window_suffix
        self.assertEqual(strip_context_window_suffix("deepseek-chat"), "deepseek-chat")
        self.assertEqual(strip_context_window_suffix(""), "")


if __name__ == "__main__":
    unittest.main()
