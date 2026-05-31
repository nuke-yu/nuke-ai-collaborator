"""
DFT-060 regression: the fork-skill branch in tool_loop_v1 was triply broken —
it called _run_fork_skill(..., usage_out=_fork_usage) but the function has no
usage_out parameter (TypeError), omitted the required ai_service argument
(TypeError), and then accumulated into undefined _total_*_tokens (NameError).
The whole fork path crashed on first use.

The fix passes the parent AIService into _run_fork_skill so the fork's child AI
call rolls its token usage into ai_service.usage (the canonical accumulator the
final message + session accounting read). These tests pin that contract.
"""
import types
import unittest
from unittest.mock import AsyncMock, patch

from core.orchestration.ai_service import AIService
from executors.plugins.tool_loop_v1 import _run_fork_skill


def _make_ai_service():
    interaction = types.SimpleNamespace(update_session_tokens=AsyncMock())
    ctx = types.SimpleNamespace(
        interaction=interaction,
        group_id=1,
        bot={"model_provider": "deepseek", "model_name": "deepseek-chat"},
    )
    return AIService(ctx, session_id="sess-1", temp_id="t-1")


class TestForkSkillUsage(unittest.IsolatedAsyncioTestCase):
    async def test_fork_tokens_roll_into_parent_ai_service_usage(self):
        ai_service = _make_ai_service()
        with patch(
            "core.orchestration.ai_service.call_ai_once",
            new=AsyncMock(return_value={
                "type": "text",
                "content": "fork done",
                "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_tokens": 2},
            }),
        ):
            # Must accept ai_service positionally and NOT raise TypeError/NameError.
            result = await _run_fork_skill(
                "skill body", "do the thing",
                "deepseek", "deepseek-chat", 0.7,
                ai_service,
            )

        self.assertEqual(result, "fork done")
        # Fork child tokens accumulated into the parent's canonical usage.
        self.assertEqual(ai_service.usage.input_tokens, 7)
        self.assertEqual(ai_service.usage.output_tokens, 3)
        self.assertEqual(ai_service.usage.cache_read_tokens, 2)

    async def test_run_fork_skill_rejects_legacy_usage_out_kwarg(self):
        """The old broken call passed usage_out=...; guard against its return."""
        ai_service = _make_ai_service()
        with self.assertRaises(TypeError):
            await _run_fork_skill(
                "skill body", "task",
                "deepseek", "deepseek-chat", 0.7,
                ai_service,
                usage_out=[],
            )


if __name__ == "__main__":
    unittest.main()
