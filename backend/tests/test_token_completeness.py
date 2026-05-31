"""Tests for token completeness in executors, specifically hook and fork token tracking."""
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _text_result(content, input_tokens=0, output_tokens=0):
    return {
        "type": "text",
        "content": content,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    }

def _make_ai_service():
    mock_ctx = MagicMock()
    mock_ctx.group_id = 1
    mock_inter = MagicMock()
    mock_inter.broadcast = AsyncMock()
    mock_inter.update_session_tokens = AsyncMock()
    mock_inter.append_session_event = AsyncMock()
    mock_inter.save_session_snapshot = AsyncMock()
    mock_ctx.interaction = mock_inter
    
    from core.orchestration.ai_service import AIService
    return AIService(mock_ctx, "session-1", "temp-1")

class TestBeforeFinalizeHookTokens(unittest.IsolatedAsyncioTestCase):

    async def test_review_tokens_collected(self):
        from executors.plugins.tool_loop_v1 import _before_finalize_hook
        ai_service = _make_ai_service()
        
        # Mock the AI call inside ai_service.call
        with patch("core.orchestration.ai_service.call_ai_once", 
                   new=AsyncMock(return_value=_text_result("APPROVED: looks good", input_tokens=8, output_tokens=3))):
            
            await _before_finalize_hook(
                draft="some draft",
                snap_messages=[{"role": "user", "content": "q"}],
                system_prompt="sp",
                config={"reviewer_prompt": "review this", "max_retries": 0},
                provider="deepseek", model_name="deepseek-chat",
                temperature=0.7, max_tokens=1024,
                ai_service=ai_service,
                user_message="user question"
            )

        self.assertEqual(ai_service.usage.input_tokens, 8)
        self.assertEqual(ai_service.usage.output_tokens, 3)

    async def test_review_and_regen_tokens_both_collected(self):
        from executors.plugins.tool_loop_v1 import _before_finalize_hook
        ai_service = _make_ai_service()

        call_count = 0
        async def mock_call(sp, msgs, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "用户问题" in msgs[0]["content"]:
                # Review call
                return _text_result("REJECTED: needs improvement", input_tokens=6, output_tokens=2)
            # Regen call
            return _text_result("improved draft", input_tokens=12, output_tokens=7)

        with patch("core.orchestration.ai_service.call_ai_once", side_effect=mock_call):
            result = await _before_finalize_hook(
                draft="original draft",
                snap_messages=[{"role": "user", "content": "q"}],
                system_prompt="sp",
                config={"reviewer_prompt": "review this", "max_retries": 1},
                provider="deepseek", model_name="deepseek-chat",
                temperature=0.7, max_tokens=1024,
                ai_service=ai_service,
                user_message="user question"
            )

        self.assertEqual(call_count, 3)
        self.assertEqual(ai_service.usage.input_tokens, 6 + 12 + 6) 
        self.assertEqual(ai_service.usage.output_tokens, 2 + 7 + 2)

class TestRunForkSkillTokens(unittest.IsolatedAsyncioTestCase):

    async def test_fork_skill_tokens_collected(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        ai_service = _make_ai_service()

        with patch("core.orchestration.ai_service.call_ai_once", 
                   new=AsyncMock(return_value=_text_result("fork result", input_tokens=14, output_tokens=5))):
            
            result = await _run_fork_skill(
                skill_content="you are a fork skill",
                task="do the thing",
                provider="deepseek",
                model="deepseek-chat",
                temperature=0.7,
                ai_service=ai_service
            )

        self.assertEqual(result, "fork result")
        self.assertEqual(ai_service.usage.input_tokens, 14)
        self.assertEqual(ai_service.usage.output_tokens, 5)

if __name__ == "__main__":
    unittest.main()
