"""Plan A §7.5.2 — fork skills run as real attenuated multi-turn sub-agents."""
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.tool_loop_v1 import _run_fork_skill


class _StubAI:
    """Returns queued results from .call(); records nothing else."""
    def __init__(self, results):
        self._results = list(results)

    async def call(self, *a, **k):
        return self._results.pop(0)


class TestForkSubagent(unittest.IsolatedAsyncioTestCase):
    async def test_multi_turn_dispatches_tools_with_attenuated_child_ctx(self):
        ai = _StubAI([
            {"type": "tool_calls",
             "assistant_message": {"role": "assistant", "content": "", "tool_calls": []},
             "calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "x"}}]},
            {"type": "text", "content": "fork final"},
        ])
        captured = {}

        async def fake_dispatch(name, args, ctx):
            captured["name"] = name
            captured["spawn_depth"] = ctx.get("spawn_depth")
            captured["has_ruleset_key"] = "ruleset" in ctx
            return ("file-bytes", False)

        with patch("executors.tool_dispatch.dispatch_tool", new=fake_dispatch):
            out = await _run_fork_skill(
                "skill body", "do it", "deepseek", "deepseek-chat", 0.7, ai,
                tool_schemas=[{"function": {"name": "read_file"}}],
                parent_ruleset=None, spawn_depth=0, group_id=1, bot_id=7,
                broadcaster=None,
            )

        self.assertEqual(out, "fork final")
        self.assertEqual(captured["name"], "read_file")
        self.assertEqual(captured["spawn_depth"], 1)   # child runs one level deeper
        self.assertTrue(captured["has_ruleset_key"])   # attenuated ruleset threaded

    async def test_no_tools_requested_returns_notice_not_silent_exec(self):
        ai = _StubAI([
            {"type": "tool_calls",
             "assistant_message": {"role": "assistant", "content": ""},
             "calls": [{"id": "c1", "name": "run_shell", "arguments": {"cmd": "rm -rf /"}}]},
        ])
        out = await _run_fork_skill(
            "body", "task", "deepseek", "deepseek-chat", 0.7, ai,
            tool_schemas=None,
        )
        self.assertIn("allowed_tools", out)
        self.assertIn("run_shell", out)

    async def test_depth_cap_refuses(self):
        ai = _StubAI([{"type": "text", "content": "should not run"}])
        out = await _run_fork_skill(
            "body", "task", "deepseek", "deepseek-chat", 0.7, ai,
            spawn_depth=999,
        )
        self.assertIn("最大深度", out)

    async def test_rejects_legacy_usage_out_kwarg(self):
        ai = _StubAI([{"type": "text", "content": "x"}])
        with self.assertRaises(TypeError):
            await _run_fork_skill("b", "t", "deepseek", "deepseek-chat", 0.7, ai,
                                  usage_out=[])

    async def test_fork_compaction_recovery_on_overflow(self):
        from ai.client import AIContextOverflowError
        from core.orchestration.ai_service import AIService
        from unittest.mock import AsyncMock, MagicMock, patch

        call_count = 0
        async def fake_call_ai_once(*a, **k):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AIContextOverflowError("context full")
            return {"type": "text", "content": "recovered text", "usage": {}}

        ctx = MagicMock()
        ctx.bot = {}
        ctx.interaction.broadcast = AsyncMock()
        ctx.interaction.update_session_tokens = AsyncMock()
        ai_service = AIService(ctx, "session-123", "temp-456")

        with patch("core.orchestration.ai_service.call_ai_once", new=fake_call_ai_once), \
             patch("executors.compact.compact_conversation", new=AsyncMock(return_value=[])):
            out = await _run_fork_skill(
                "body", "task", "deepseek", "deepseek-chat", 0.7, ai_service,
                tool_schemas=None,
            )

        self.assertEqual(out, "recovered text")
        self.assertEqual(call_count, 2)


if __name__ == "__main__":
    unittest.main()
