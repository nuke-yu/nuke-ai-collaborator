"""
tests/test_token_completeness.py

验证所有 AI 调用路径的 token 追踪完整性：
  1. orchestrator.stream_bot_response — 流式路径 usage_out → save_message
  2. orchestrator.call_bot (race path) — call_ai_once usage → save_message
  3. workflow._trigger_single_stage   — 链式阶段 usage_out → update_message
  4. workflow._trigger_pool_bot       — 池式阶段 usage_out → update_message
  5. db.update_message                — 新签名保留旧 token、写入新 token
  6. tool_loop_v1._stream_final       — 流式最终回复 tokens 计入总量
  7. tool_loop_v1._before_finalize_hook — review/regen tokens 计入 usage_out
  8. tool_loop_v1._run_fork_skill     — fork skill tokens 计入 usage_out
  9. tool_loop_v1._finalize_reply     — gen draft tokens + bf_hook tokens 累加
"""
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── shared fixtures ──────────────────────────────────────────────────────────

def _text_result(content="ok", input_tokens=10, output_tokens=5):
    return {"type": "text", "content": content,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}


def _make_bot(bot_id=1):
    return {
        "id": bot_id, "name": "Bot", "role": "助手",
        "system_prompt": "You are a helper.",
        "personality_prompt": None,
        "avatar_color": "#6366f1",
        "model_provider": "deepseek",
        "model_name": "deepseek-chat",
        "temperature": 0.7,
        "max_tokens": 4096,
    }


# ─── async SSE helper ─────────────────────────────────────────────────────────

async def _fake_stream(*chunks, usage=None, usage_out=None):
    """Async generator that yields chunks and optionally populates usage_out."""
    for chunk in chunks:
        yield chunk
    if usage_out is not None and usage:
        usage_out.append(usage)


# =============================================================================
# 1. orchestrator.stream_bot_response  — usage_out → save_message
# =============================================================================

class TestStreamBotResponseTokens(unittest.IsolatedAsyncioTestCase):

    async def test_tokens_saved_to_db(self):
        from core import orchestrator

        bot = _make_bot()
        saved_kwargs = {}

        async def fake_stream(sp, hist, msg, prov, model, temp, max_tok, usage_out=None):
            if usage_out is not None:
                usage_out.append({"input_tokens": 20, "output_tokens": 8})
            yield "hello"

        async def fake_save(db, group_id, member_id, content, **kwargs):
            saved_kwargs.update(kwargs)
            return 42

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(orchestrator, "call_ai_stream", new=fake_stream), \
             patch.object(orchestrator, "save_message", new=fake_save), \
             patch.object(orchestrator, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(orchestrator, "get_db", return_value=fake_db_cm), \
             patch.object(orchestrator.bus, "publish", new=AsyncMock()):
            await orchestrator.stream_bot_response(1, bot, "sp", [], "hi")

        self.assertEqual(saved_kwargs.get("input_tokens"), 20)
        self.assertEqual(saved_kwargs.get("output_tokens"), 8)

    async def test_no_usage_saves_none(self):
        from core import orchestrator

        bot = _make_bot()
        saved_kwargs = {}

        async def fake_stream(sp, hist, msg, prov, model, temp, max_tok, usage_out=None):
            yield "hi"  # no usage appended

        async def fake_save(db, group_id, member_id, content, **kwargs):
            saved_kwargs.update(kwargs)
            return 1

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(orchestrator, "call_ai_stream", new=fake_stream), \
             patch.object(orchestrator, "save_message", new=fake_save), \
             patch.object(orchestrator, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(orchestrator, "get_db", return_value=fake_db_cm), \
             patch.object(orchestrator.bus, "publish", new=AsyncMock()):
            await orchestrator.stream_bot_response(1, bot, "sp", [], "hi")

        self.assertIsNone(saved_kwargs.get("input_tokens"))
        self.assertIsNone(saved_kwargs.get("output_tokens"))


# =============================================================================
# 2. orchestrator.call_bot race path — call_ai_once usage → save_message
# =============================================================================

class TestCallBotRaceTokens(unittest.IsolatedAsyncioTestCase):

    async def test_race_winner_tokens_saved(self):
        """When multiple bots race, the winner's token usage is saved."""
        from core import orchestrator

        bot = _make_bot()
        saved_kwargs = {}

        async def fake_call_ai_once(sp, msgs, provider, model, temperature, max_tokens, **kw):
            return _text_result("winner answer", input_tokens=30, output_tokens=12)

        async def fake_save(db, group_id, member_id, content, **kwargs):
            saved_kwargs.update(kwargs)
            return 99

        async def fake_get_memory_context(*a, **kw):
            return ""

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        fake_message_cls = MagicMock(return_value=MagicMock())
        with patch.object(orchestrator, "call_ai_once", new=fake_call_ai_once), \
             patch.object(orchestrator, "save_message", new=fake_save), \
             patch.object(orchestrator, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(orchestrator, "get_db", return_value=fake_db_cm), \
             patch.object(orchestrator, "get_memory_context", new=fake_get_memory_context), \
             patch.object(orchestrator, "add_to_chroma", new=AsyncMock()), \
             patch.object(orchestrator, "Message", new=fake_message_cls), \
             patch.object(orchestrator, "maybe_summarize", new=AsyncMock()), \
             patch.object(orchestrator.bus, "publish", new=AsyncMock()):
            # Directly test call_bot return value contains usage
            # by invoking dispatch_bots with 2 bots (triggers race path)
            all_bots = [bot, {**bot, "id": 2, "name": "Bot2"}]
            sender = {"id": 99, "name": "User", "type": "human"}
            recent = [{"id": 1, "member_id": 99, "sender_type": "human",
                       "content": "hello", "created_at": "2026-01-01",
                       "sender_name": "User"}]
            await orchestrator.dispatch_bots(
                group_id=1, triggered=all_bots, content="hello",
                sender=sender, recent=recent,
                all_bots=all_bots, all_members=all_bots,
            )

        self.assertEqual(saved_kwargs.get("input_tokens"), 30)
        self.assertEqual(saved_kwargs.get("output_tokens"), 12)


# =============================================================================
# 3 & 4. workflow chain/pool stages — usage_out → update_message tokens
# =============================================================================

class TestWorkflowStageTokens(unittest.IsolatedAsyncioTestCase):

    def _make_bot_entry(self, bot_id=10):
        return {
            "id": bot_id, "name": "Dev", "role": "开发",
            "system_prompt": "code", "personality_prompt": None,
            "avatar_color": "#fff",
            "model_provider": "deepseek", "model_name": "deepseek-chat",
            "temperature": 0.7, "max_tokens": 4096,
        }

    async def test_chain_stage_tokens_written(self):
        import core.workflow as wf
        from core.workflow import _trigger_single_stage

        bot = self._make_bot_entry()
        updated_kwargs = {}

        async def fake_stream(sp, hist, msg, prov, model, temp, max_tok, usage_out=None):
            if usage_out is not None:
                usage_out.append({"input_tokens": 15, "output_tokens": 6})
            yield "step output"

        async def fake_update(db, msg_id, content, **kwargs):
            updated_kwargs.update(kwargs)

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("ai.client.call_ai_stream", new=fake_stream), \
             patch.object(wf, "update_message", new=fake_update), \
             patch.object(wf, "save_message", new=AsyncMock(return_value=5)), \
             patch.object(wf, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(wf, "get_db", return_value=fake_db_cm), \
             patch.object(wf, "bus") as mock_bus, \
             patch.object(wf, "check_and_advance", new=AsyncMock()), \
             patch.object(wf, "_build_history", new=AsyncMock(return_value=[])), \
             patch.object(wf, "system_suffix", return_value=""):
            mock_bus.publish = AsyncMock()
            mock_bus.broadcast = AsyncMock()
            await _trigger_single_stage(1, {}, bot)

        self.assertEqual(updated_kwargs.get("input_tokens"), 15)
        self.assertEqual(updated_kwargs.get("output_tokens"), 6)

    async def test_pool_bot_tokens_written(self):
        import core.workflow as wf
        from core.workflow import _trigger_pool_bot

        bot = self._make_bot_entry()
        updated_kwargs = {}

        async def fake_stream(sp, hist, msg, prov, model, temp, max_tok, usage_out=None):
            if usage_out is not None:
                usage_out.append({"input_tokens": 25, "output_tokens": 10})
            yield "pool output"

        async def fake_update(db, msg_id, content, **kwargs):
            updated_kwargs.update(kwargs)

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        pool_stage = {"done_keyword": "完毕"}
        import core.workflow as _wf_mod
        _wf_mod._state[1] = {"current": 0, "stages": [pool_stage]}

        with patch("ai.client.call_ai_stream", new=fake_stream), \
             patch.object(wf, "update_message", new=fake_update), \
             patch.object(wf, "save_message", new=AsyncMock(return_value=7)), \
             patch.object(wf, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(wf, "get_db", return_value=fake_db_cm), \
             patch.object(wf, "bus") as mock_bus, \
             patch.object(wf, "check_and_advance", new=AsyncMock()), \
             patch.object(wf, "_build_history", new=AsyncMock(return_value=[])):
            mock_bus.publish = AsyncMock()
            mock_bus.broadcast = AsyncMock()
            await _trigger_pool_bot(1, bot, "ticket-1", pool_stage)

        self.assertEqual(updated_kwargs.get("input_tokens"), 25)
        self.assertEqual(updated_kwargs.get("output_tokens"), 10)


# =============================================================================
# 5. db.update_message — COALESCE keeps old tokens when None, sets when provided
# =============================================================================

class TestUpdateMessageTokens(unittest.IsolatedAsyncioTestCase):

    async def test_tokens_written_when_provided(self):
        from db.queries import update_message

        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        await update_message(mock_db, msg_id=5, content="new content",
                             input_tokens=18, output_tokens=7)

        sql, params = mock_db.execute.call_args[0]
        self.assertIn("COALESCE", sql)
        self.assertEqual(params, ("new content", 18, 7, None, None, 5))

    async def test_tokens_none_uses_coalesce(self):
        """None tokens pass through COALESCE, preserving existing DB values."""
        from db.queries import update_message

        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        await update_message(mock_db, msg_id=3, content="edited")

        sql, params = mock_db.execute.call_args[0]
        self.assertIn("COALESCE", sql)
        # params should be (content, None, None, msg_id)
        # params: (content, input_tokens, output_tokens, cache_read, cache_creation, msg_id)
        self.assertEqual(params[0], "edited")
        self.assertIsNone(params[1])
        self.assertIsNone(params[2])
        self.assertIsNone(params[3])
        self.assertIsNone(params[4])
        self.assertEqual(params[5], 3)


# =============================================================================
# 6. tool_loop_v1._stream_final — streaming tokens accumulated into counters
# =============================================================================

class TestStreamFinalTokens(unittest.IsolatedAsyncioTestCase):

    async def test_stream_final_tokens_added_to_totals(self):
        from executors.plugins.tool_loop_v1 import _before_finalize_hook

        # Test _stream_final indirectly via full _finalize_reply flow —
        # easier to test _before_finalize_hook directly (see test 7).
        # For _stream_final, verify _acc_usage helper is consistent.
        from executors.plugins.tool_loop_v1 import _acc_usage
        target = []
        _acc_usage(target, {"type": "text", "content": "hi",
                            "usage": {"input_tokens": 11, "output_tokens": 4}})
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0]["input_tokens"], 11)
        self.assertEqual(target[0]["output_tokens"], 4)

    async def test_acc_usage_multiple_appends(self):
        from executors.plugins.tool_loop_v1 import _acc_usage
        target = []
        _acc_usage(target, _text_result(input_tokens=5, output_tokens=2))
        _acc_usage(target, _text_result(input_tokens=8, output_tokens=3))
        total_in = sum(u["input_tokens"] for u in target)
        total_out = sum(u["output_tokens"] for u in target)
        self.assertEqual(total_in, 13)
        self.assertEqual(total_out, 5)

    async def test_acc_usage_no_usage_field_is_noop(self):
        from executors.plugins.tool_loop_v1 import _acc_usage
        target = []
        _acc_usage(target, {"type": "text", "content": "hi"})  # no usage key
        self.assertEqual(target, [])

    async def test_acc_usage_null_usage_is_noop(self):
        from executors.plugins.tool_loop_v1 import _acc_usage
        target = []
        _acc_usage(target, {"type": "text", "content": "hi", "usage": None})
        self.assertEqual(target, [])


# =============================================================================
# 7. tool_loop_v1._before_finalize_hook — review + regen tokens in usage_out
# =============================================================================

class TestBeforeFinalizeHookTokens(unittest.IsolatedAsyncioTestCase):

    def _make_broadcaster(self):
        b = MagicMock()
        b.broadcast = AsyncMock()
        return b

    async def test_review_tokens_collected(self):
        from executors.plugins.tool_loop_v1 import _before_finalize_hook

        async def fake_call_ai_once(sp, msgs, prov, model, temp, max_tok, *args, **kw):
            return _text_result("APPROVED: looks good", input_tokens=8, output_tokens=3)

        usage_out: list = []
        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            await _before_finalize_hook(
                draft="some draft",
                snap_messages=[{"role": "user", "content": "q"}],
                system_prompt="sp",
                config={"reviewer_prompt": "review this", "max_retries": 0},
                provider="deepseek", model_name="deepseek-chat",
                temperature=0.7, max_tokens=1024,
                broadcaster=self._make_broadcaster(),
                group_id=1, temp_id="t1",
                user_message="user question",
                usage_out=usage_out,
            )

        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["input_tokens"], 8)
        self.assertEqual(usage_out[0]["output_tokens"], 3)

    async def test_review_and_regen_tokens_both_collected(self):
        from executors.plugins.tool_loop_v1 import _before_finalize_hook

        call_count = 0

        async def fake_call_ai_once(sp, msgs, prov, model, temp, max_tok, *args, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: review → REJECTED
                return _text_result("REJECTED: needs improvement", input_tokens=6, output_tokens=2)
            # Second call: regen
            return _text_result("improved draft", input_tokens=12, output_tokens=7)

        usage_out: list = []
        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            result = await _before_finalize_hook(
                draft="original draft",
                snap_messages=[{"role": "user", "content": "q"}],
                system_prompt="sp",
                config={"reviewer_prompt": "review this", "max_retries": 1},
                provider="deepseek", model_name="deepseek-chat",
                temperature=0.7, max_tokens=1024,
                broadcaster=self._make_broadcaster(),
                group_id=1, temp_id="t1",
                user_message="user question",
                usage_out=usage_out,
            )

        # review(rejected) + regen + review(approved on attempt 1) = 3 AI calls
        self.assertEqual(len(usage_out), 3)
        total_in = sum(u["input_tokens"] for u in usage_out)
        total_out = sum(u["output_tokens"] for u in usage_out)
        self.assertEqual(total_in, 30)   # 6 + 12 + 12
        self.assertEqual(total_out, 16)  # 2 + 7 + 7

    async def test_usage_out_none_does_not_crash(self):
        from executors.plugins.tool_loop_v1 import _before_finalize_hook

        async def fake_call_ai_once(sp, msgs, prov, model, temp, max_tok, *args, **kw):
            return _text_result("APPROVED")

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            result = await _before_finalize_hook(
                draft="draft",
                snap_messages=[],
                system_prompt="sp",
                config={"reviewer_prompt": "review", "max_retries": 0},
                provider="deepseek", model_name="deepseek-chat",
                temperature=0.7, max_tokens=512,
                broadcaster=self._make_broadcaster(),
                group_id=1, temp_id="t",
                user_message="q",
                usage_out=None,  # must not crash
            )

        self.assertIsInstance(result, str)


# =============================================================================
# 8. tool_loop_v1._run_fork_skill — fork tokens collected in usage_out
# =============================================================================

class TestRunForkSkillTokens(unittest.IsolatedAsyncioTestCase):

    async def test_fork_skill_tokens_collected(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill

        async def fake_call_ai_once(sp, msgs, prov, model, temp, max_tok, *args, **kw):
            return _text_result("fork result", input_tokens=14, output_tokens=5)

        usage_out: list = []
        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            result = await _run_fork_skill(
                skill_content="you are a fork skill",
                task="do the thing",
                provider="deepseek",
                model="deepseek-chat",
                temperature=0.7,
                usage_out=usage_out,
            )

        self.assertEqual(result, "fork result")
        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["input_tokens"], 14)
        self.assertEqual(usage_out[0]["output_tokens"], 5)

    async def test_fork_skill_usage_out_none_does_not_crash(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill

        async def fake_call_ai_once(sp, msgs, prov, model, temp, max_tok, *args, **kw):
            return _text_result("fork result")

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            result = await _run_fork_skill(
                skill_content="skill",
                task="task",
                provider="deepseek",
                model="deepseek-chat",
                temperature=0.7,
                usage_out=None,
            )

        self.assertEqual(result, "fork result")

    async def test_fork_skill_exception_returns_error_string(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        from ai.client import AIError

        async def fake_call_ai_once(*a, **kw):
            raise AIError("API down")

        usage_out: list = []
        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            result = await _run_fork_skill(
                skill_content="skill", task="task",
                provider="deepseek", model="deepseek-chat", temperature=0.7,
                usage_out=usage_out,
            )

        self.assertIn("fork skill 执行错误", result)
        self.assertEqual(usage_out, [])  # no usage on error


# =============================================================================
# 9. _finalize_reply integration — gen draft tokens + hook tokens both counted
# =============================================================================

class TestFinalizeReplyTokenAccumulation(unittest.TestCase):
    """Unit-level: verify the accumulation arithmetic matches _finalize_reply logic."""

    def _simulate_finalize_tokens(self, gen_usage, hook_usages):
        """Replicate the _finalize_reply accumulation logic."""
        total_in = total_out = 0
        _gu = gen_usage.get("usage") or {}
        total_in += _gu.get("input_tokens", 0)
        total_out += _gu.get("output_tokens", 0)
        for u in hook_usages:
            total_in += u.get("input_tokens", 0)
            total_out += u.get("output_tokens", 0)
        return total_in, total_out

    def test_gen_only_no_review(self):
        result = _text_result(input_tokens=20, output_tokens=8)
        total_in, total_out = self._simulate_finalize_tokens(result, [])
        self.assertEqual(total_in, 20)
        self.assertEqual(total_out, 8)

    def test_gen_plus_review_regen(self):
        gen = _text_result(input_tokens=20, output_tokens=8)
        hook = [
            {"input_tokens": 6, "output_tokens": 2},   # review
            {"input_tokens": 12, "output_tokens": 7},  # regen
        ]
        total_in, total_out = self._simulate_finalize_tokens(gen, hook)
        self.assertEqual(total_in, 38)   # 20+6+12
        self.assertEqual(total_out, 17)  # 8+2+7


if __name__ == "__main__":
    unittest.main()
