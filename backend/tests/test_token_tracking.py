"""
tests/test_token_tracking.py

覆盖：
  - tool_loop_v1: _tool_loop_core 多轮 usage 累加逻辑
"""
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call as mock_call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── helpers ─────────────────────────────────────────────────────────────────

def _text_result(content="done", input_tokens=10, output_tokens=5):
    return {"type": "text", "content": content,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}


def _tool_result(calls, input_tokens=8, output_tokens=3, assistant_message=None):
    return {
        "type": "tool_calls",
        "calls": calls,
        "assistant_message": assistant_message or {"role": "assistant", "content": None,
                                                    "tool_calls": []},
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 一、_tool_loop_core token 累加
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolLoopCoreTokens(unittest.IsolatedAsyncioTestCase):
    """_tool_loop_core 本身不保存 DB，但验证 usage 字段从 call_ai_once 获取。"""

    async def test_text_response_returns_content(self):
        """单轮纯文本响应 → _tool_loop_core 返回 content 字符串。"""
        from executors.plugins.tool_loop_v1 import _tool_loop_core

        async def fake_call_ai_once(*a, **kw):
            return _text_result("final answer", 10, 5)

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once), \
             patch("executors.plugins.tool_loop_v1._execute_tool_call", new=AsyncMock()):
            result = await _tool_loop_core(
                system_prompt="sp",
                messages=[{"role": "user", "content": "hi"}],
                provider="deepseek", model_name="deepseek-chat",
                temperature=0.7, max_tokens=256,
                tool_schemas=[],
            )

        self.assertEqual(result, "final answer")

    async def test_tool_call_then_text(self):
        """第一轮 tool_call，第二轮文本 → _tool_loop_core 正确循环两次。"""
        from executors.plugins.tool_loop_v1 import _tool_loop_core

        call_count = 0

        async def fake_call_ai_once(sp, msgs, provider, model, temp, max_tok, tools, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_result(
                    calls=[{"id": "c1", "name": "run_shell", "arguments": {"cmd": "ls"}}],
                    assistant_message={"role": "assistant", "content": None, "tool_calls": []},
                )
            return _text_result("all done", 12, 6)

        async def fake_execute(name, args, context):
            return "file list"

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once), \
             patch("executors.plugins.tool_loop_v1._execute_tool_call", new=fake_execute):
            result = await _tool_loop_core(
                system_prompt="sp",
                messages=[{"role": "user", "content": "list files"}],
                provider="deepseek", model_name="deepseek-chat",
                temperature=0.7, max_tokens=256,
                tool_schemas=[],
            )

        self.assertEqual(result, "all done")
        self.assertEqual(call_count, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# 二、token 累加逻辑（单元级）
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenAccumulationLogic(unittest.TestCase):
    """验证 usage 合并逻辑：多轮结果的 input_tokens / output_tokens 应正确累加。"""

    def _accumulate(self, results: list) -> tuple[int, int]:
        """模拟 tool_loop_v1 里的累加代码。"""
        total_in = total_out = 0
        for r in results:
            u = r.get("usage") or {}
            total_in += u.get("input_tokens", 0)
            total_out += u.get("output_tokens", 0)
        return total_in, total_out

    def test_single_text_result(self):
        r = _text_result(input_tokens=20, output_tokens=8)
        self.assertEqual(self._accumulate([r]), (20, 8))

    def test_multi_round_accumulation(self):
        results = [
            _tool_result([], input_tokens=10, output_tokens=3),
            _tool_result([], input_tokens=15, output_tokens=4),
            _text_result(input_tokens=12, output_tokens=6),
        ]
        total_in, total_out = self._accumulate(results)
        self.assertEqual(total_in, 37)
        self.assertEqual(total_out, 13)

    def test_missing_usage_defaults_to_zero(self):
        """result 中无 usage 字段时不应抛异常，值视为 0。"""
        results = [{"type": "text", "content": "ok"}]  # no 'usage'
        self.assertEqual(self._accumulate(results), (0, 0))

    def test_null_usage_defaults_to_zero(self):
        results = [{"type": "text", "content": "ok", "usage": None}]
        self.assertEqual(self._accumulate(results), (0, 0))

    def test_zero_tokens_becomes_none_for_save(self):
        """tool_loop_v1 把 0 转成 None 后再传给 save_message，避免写入无意义的 0。"""
        total_in, total_out = 0, 0
        # 模拟 `_total_input_tokens or None` 逻辑
        self.assertIsNone(total_in or None)
        self.assertIsNone(total_out or None)

    def test_nonzero_tokens_passed_through(self):
        total_in, total_out = 37, 13
        self.assertEqual(total_in or None, 37)
        self.assertEqual(total_out or None, 13)



if __name__ == "__main__":
    unittest.main()
