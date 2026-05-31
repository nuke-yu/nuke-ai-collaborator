"""
DFT-064 / DFT-035 regression: the overflow-recovery path (ai_service ->
compact.compact_conversation) must not split an assistant(tool_calls) from its
tool results. If it does, the retried request carries an orphan tool_call_id and
Claude rejects it with 400, killing the run.

The dead `_overflow_recovered` guard in tool_loop_v1 was removed in DFT-064; the
actual protection lives in compact_conversation's split-boundary walk (it steps
left past `tool` messages so the kept tail never starts with an orphan tool).
This test locks that behaviour.
"""
import unittest
from unittest.mock import AsyncMock, patch

import executors.compact as compact


def _assert_no_orphan_tool(self, msgs):
    """Every `tool` message must follow an assistant (or another tool)."""
    for i, m in enumerate(msgs):
        if m.get("role") == "tool":
            self.assertGreater(i, 0, f"tool at index 0 is orphaned: {msgs}")
            self.assertIn(
                msgs[i - 1].get("role"), ("assistant", "tool"),
                f"orphan tool at index {i} (preceded by {msgs[i-1].get('role')}): {msgs}",
            )


class TestCompactOverflowPairing(unittest.IsolatedAsyncioTestCase):
    async def test_split_boundary_never_orphans_tool_results(self):
        # Naive split (len - keep_recent=6 -> index 4) lands ON the tool message;
        # without the boundary walk the kept tail would start with an orphan tool.
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "type": "function"}]},
            {"role": "tool", "tool_call_id": "tc1", "content": "result1"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "q4"},
            {"role": "assistant", "content": "a4"},
        ]

        with patch("executors.compact._ai_compact",
                   new=AsyncMock(return_value=[{"role": "user", "content": "[summary]"}])):
            result = await compact.compact_conversation(
                messages, "sys", "deepseek", "deepseek-chat", 0.7, keep_recent=6,
            )

        _assert_no_orphan_tool(self, result)
        # The tool result must still be paired with its assistant(tool_calls).
        roles = [m.get("role") for m in result]
        self.assertIn("tool", roles)
        tool_idx = roles.index("tool")
        self.assertEqual(result[tool_idx - 1].get("role"), "assistant")
        self.assertIn("tool_calls", result[tool_idx - 1])

    async def test_short_history_returned_unchanged(self):
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        result = await compact.compact_conversation(
            messages, "sys", "deepseek", "deepseek-chat", 0.7, keep_recent=6,
        )
        self.assertEqual(result, messages)


if __name__ == "__main__":
    unittest.main()
