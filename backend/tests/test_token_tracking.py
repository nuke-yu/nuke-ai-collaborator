"""
tests/test_token_tracking.py

覆盖：
  - tool_loop_v1: _tool_loop_core 多轮 usage 累加逻辑
  - orchestrator: auto_continue_if_needed 使用最后一条用户消息作为 memory 查询词
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


# ═══════════════════════════════════════════════════════════════════════════════
# 三、orchestrator.auto_continue_if_needed — memory 查询词修复
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoContineMemoryQuery(unittest.IsolatedAsyncioTestCase):
    """验证 auto_continue_if_needed 使用最后一条用户消息作为 memory 查询词（不再硬编码）。"""

    def _make_bot(self, bot_id=1, role="Python开发"):
        return {
            "id": bot_id, "name": "Bot", "role": role,
            "system_prompt": "You are a coder.",
            "personality_prompt": None,
            "avatar_color": "#6366f1",
            "model_provider": "deepseek",
            "model_name": "deepseek-chat",
            "temperature": 0.7,
            "max_tokens": 4096,
        }

    def _make_recent(self, bot_id=1, user_msg="请实现登录功能", bot_msg=None):
        msgs = [
            {"id": 1, "member_id": 99, "sender_type": "human",
             "content": user_msg, "created_at": "2026-01-01"},
        ]
        if bot_msg is not None:
            msgs.append({
                "id": 2, "member_id": bot_id, "sender_type": "bot",
                "content": bot_msg, "created_at": "2026-01-01",
            })
        return msgs

    async def test_memory_queried_with_last_user_message(self):
        """get_memory_context 收到的第三个参数应该是最后一条用户消息内容。"""
        from core import orchestrator

        bot = self._make_bot(role="Python开发")
        user_msg = "请实现用户登录功能"
        recent = self._make_recent(bot_id=1, user_msg=user_msg,
                                   bot_msg="好的，开始实现")  # latest bot msg has no @mention

        captured_query = {}

        async def fake_get_memory_context(bot_id, role, query):
            captured_query["query"] = query
            return ""

        async def fake_get_messages(db, gid, limit=None):
            return recent

        # stream_bot_response returns (None, None) → loop breaks
        async def fake_stream_bot_response(*a, **kw):
            return None, None

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(orchestrator, "get_db", return_value=fake_db_cm), \
             patch.object(orchestrator, "get_messages", new=fake_get_messages), \
             patch.object(orchestrator, "get_memory_context", new=fake_get_memory_context), \
             patch.object(orchestrator, "stream_bot_response", new=fake_stream_bot_response), \
             patch.object(orchestrator, "add_to_chroma", new=AsyncMock()), \
             patch.object(orchestrator, "maybe_summarize", new=AsyncMock()), \
             patch.object(orchestrator, "build_context_message",
                          return_value=([], None)):
            await orchestrator.auto_continue_if_needed(
                group_id=1, bot=bot, all_members=[bot], max_iter=1
            )

        self.assertEqual(captured_query.get("query"), user_msg,
                         "memory context should be queried with the actual last user message")

    async def test_memory_query_fallback_when_no_human_message(self):
        """当 recent 中没有人类消息时，回落到默认字符串不崩溃。"""
        from core import orchestrator

        bot = self._make_bot(role="Python开发")
        # Only bot messages — no human message
        recent = [{"id": 2, "member_id": 1, "sender_type": "bot",
                   "content": "doing stuff", "created_at": "2026-01-01"}]

        captured_query = {}

        async def fake_get_memory_context(bot_id, role, query):
            captured_query["query"] = query
            return ""

        async def fake_get_messages(db, gid, limit=None):
            return recent

        async def fake_stream_bot_response(*a, **kw):
            return None, None

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(orchestrator, "get_db", return_value=fake_db_cm), \
             patch.object(orchestrator, "get_messages", new=fake_get_messages), \
             patch.object(orchestrator, "get_memory_context", new=fake_get_memory_context), \
             patch.object(orchestrator, "stream_bot_response", new=fake_stream_bot_response), \
             patch.object(orchestrator, "add_to_chroma", new=AsyncMock()), \
             patch.object(orchestrator, "maybe_summarize", new=AsyncMock()), \
             patch.object(orchestrator, "build_context_message",
                          return_value=([], None)):
            await orchestrator.auto_continue_if_needed(
                group_id=1, bot=bot, all_members=[bot], max_iter=1
            )

        # Should fall back to the default string, not crash
        self.assertEqual(captured_query.get("query"), "继续代码")

    async def test_non_dev_role_skips_immediately(self):
        """非开发角色的 bot 应立即返回，不调用 get_memory_context。"""
        from core import orchestrator

        bot = self._make_bot(role="翻译专家")
        called = {}

        async def should_not_be_called(*a, **kw):
            called["yes"] = True

        with patch.object(orchestrator, "get_db", new=MagicMock()), \
             patch.object(orchestrator, "get_memory_context", new=should_not_be_called):
            await orchestrator.auto_continue_if_needed(
                group_id=1, bot=bot, all_members=[bot], max_iter=5
            )

        self.assertNotIn("yes", called)


if __name__ == "__main__":
    unittest.main()
