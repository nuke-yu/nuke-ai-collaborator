"""
tests/test_cache_tokens.py

验证 cache token 追踪的完整性：
  1. _once_openai_compat  — DeepSeek prompt_cache_hit_tokens
  2. _once_openai_compat  — OpenAI prompt_tokens_details.cached_tokens
  3. _once_claude         — cache_read_input_tokens + cache_creation_input_tokens
  4. _stream_openai_compat — 流式 DeepSeek cache 字段
  5. _stream_openai_compat — 流式 OpenAI prompt_tokens_details
  6. _stream_claude        — message_start cache 字段
  7. _acc_usage            — 包含 cache 字段
  8. save_message          — SQL 中含 cache 字段
  9. update_message        — COALESCE 对 cache 字段同样生效
 10. _row_to_msg           — SELECT 结果含 cache 字段
 11. simple_v1             — cache tokens 透传到 save_message
 12. react_v1              — cache tokens 透传到 save_message
"""
import sys
import os
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── streaming mock helper ────────────────────────────────────────────────────

def _make_stream_mock(sse_lines: list):
    """Build the httpx async-stream mock chain used by _stream_* functions."""
    async def aiter_lines():
        for line in sse_lines:
            yield line

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = aiter_lines

    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    stream_cm.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=stream_cm)

    return mock_client


# =============================================================================
# 1 & 2. _once_openai_compat — DeepSeek + OpenAI cache fields
# =============================================================================

class TestOnceOpenAICompatCacheTokens(unittest.IsolatedAsyncioTestCase):

    async def _call(self, usage_payload: dict) -> dict:
        from ai.client import _once_openai_compat
        resp_json = {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": usage_payload,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=resp_json)

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        # Patch _get_client instead of httpx.AsyncClient because client.py now uses a shared singleton
        with patch("ai.client._get_client", return_value=mock_client):
            return await _once_openai_compat(
                "https://api.deepseek.com/v1/chat/completions",
                "sk-key", "deepseek-chat",
                "sys", [], 0.7, 4096, None,
            )

    async def test_deepseek_cache_hit_tokens(self):
        result = await self._call({
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
        })
        self.assertEqual(result["usage"]["cache_read_tokens"], 80)
        self.assertEqual(result["usage"]["cache_creation_tokens"], 0)
        self.assertEqual(result["usage"]["input_tokens"], 100)

    async def test_openai_prompt_tokens_details(self):
        result = await self._call({
            "prompt_tokens": 150,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 120},
        })
        self.assertEqual(result["usage"]["cache_read_tokens"], 120)
        self.assertEqual(result["usage"]["cache_creation_tokens"], 0)

    async def test_no_cache_fields_returns_zero(self):
        result = await self._call({
            "prompt_tokens": 50,
            "completion_tokens": 10,
        })
        self.assertEqual(result["usage"]["cache_read_tokens"], 0)
        self.assertEqual(result["usage"]["cache_creation_tokens"], 0)


# =============================================================================
# 3. _once_claude — cache_read_input_tokens + cache_creation_input_tokens
# =============================================================================

class TestOnceClaudeCacheTokens(unittest.IsolatedAsyncioTestCase):

    async def _call(self, usage_payload: dict) -> dict:
        from ai.client import _once_claude
        resp_json = {
            "content": [{"type": "text", "text": "hi"}],
            "usage": usage_payload,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=resp_json)

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("ai.client._get_client", return_value=mock_client):
            return await _once_claude(
                "sk-ant-key", "claude-3-5-sonnet-20241022",
                "sys", [], 0.7, 4096, None,
            )

    async def test_cache_read_and_creation(self):
        result = await self._call({
            "input_tokens": 200,
            "output_tokens": 50,
            "cache_read_input_tokens": 150,
            "cache_creation_input_tokens": 30,
        })
        self.assertEqual(result["usage"]["cache_read_tokens"], 150)
        self.assertEqual(result["usage"]["cache_creation_tokens"], 30)
        self.assertEqual(result["usage"]["input_tokens"], 200)
        self.assertEqual(result["usage"]["output_tokens"], 50)

    async def test_no_cache_fields_returns_zero(self):
        result = await self._call({
            "input_tokens": 100,
            "output_tokens": 25,
        })
        self.assertEqual(result["usage"]["cache_read_tokens"], 0)
        self.assertEqual(result["usage"]["cache_creation_tokens"], 0)


# =============================================================================
# 4 & 5. _stream_openai_compat — DeepSeek + OpenAI streaming cache fields
# =============================================================================

class TestStreamOpenAICompatCacheTokens(unittest.IsolatedAsyncioTestCase):

    async def _stream(self, usage_payload: dict) -> list:
        from ai.client import _stream_openai_compat
        usage_line = "data: " + json.dumps({
            "choices": [{"delta": {"content": ""}}],
            "usage": usage_payload,
        })
        mock_client = _make_stream_mock([
            'data: {"choices":[{"delta":{"content":"hello"}}]}',
            usage_line,
            "data: [DONE]",
        ])
        usage_out = []
        with patch("ai.client._get_client", return_value=mock_client):
            async for _ in _stream_openai_compat(
                "https://api.deepseek.com/v1/chat/completions",
                "sk-key", "deepseek-chat", [],
                usage_out=usage_out,
            ):
                pass
        return usage_out

    async def test_deepseek_cache_hit_in_stream(self):
        usage_out = await self._stream({
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 75,
        })
        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["cache_read_tokens"], 75)
        self.assertEqual(usage_out[0]["cache_creation_tokens"], 0)

    async def test_openai_prompt_tokens_details_in_stream(self):
        usage_out = await self._stream({
            "prompt_tokens": 200,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 180},
        })
        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["cache_read_tokens"], 180)

    async def test_no_cache_in_stream_returns_zero(self):
        usage_out = await self._stream({
            "prompt_tokens": 50,
            "completion_tokens": 10,
        })
        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["cache_read_tokens"], 0)


# =============================================================================
# 6. _stream_claude — message_start cache fields
# =============================================================================

class TestStreamClaudeCacheTokens(unittest.IsolatedAsyncioTestCase):

    async def _stream_claude(self, start_usage: dict) -> list:
        from ai.client import _stream_claude
        lines = [
            "data: " + json.dumps({"type": "message_start", "message": {"usage": start_usage}}),
            'data: {"type":"content_block_delta","delta":{"text":"hi"}}',
            'data: {"type":"message_delta","usage":{"output_tokens":5}}',
        ]
        mock_client = _make_stream_mock(lines)
        usage_out = []
        with patch("ai.client._get_client", return_value=mock_client):
            async for _ in _stream_claude(
                "claude-3-5-sonnet-20241022", "sys", [], "sk-ant-key",
                usage_out=usage_out,
            ):
                pass
        return usage_out

    async def test_cache_fields_in_message_start(self):
        usage_out = await self._stream_claude({
            "input_tokens": 300,
            "cache_read_input_tokens": 250,
            "cache_creation_input_tokens": 40,
        })
        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["cache_read_tokens"], 250)
        self.assertEqual(usage_out[0]["cache_creation_tokens"], 40)
        self.assertEqual(usage_out[0]["input_tokens"], 300)
        self.assertEqual(usage_out[0]["output_tokens"], 5)

    async def test_no_cache_fields_returns_zero(self):
        usage_out = await self._stream_claude({
            "input_tokens": 100,
        })
        self.assertEqual(usage_out[0]["cache_read_tokens"], 0)
        self.assertEqual(usage_out[0]["cache_creation_tokens"], 0)


# =============================================================================
# 7. _acc_usage — includes cache fields
# =============================================================================

class TestAccUsageCacheFields(unittest.TestCase):

    def _make_result(self, in_tok, out_tok, cache_read=0, cache_creation=0):
        return {"type": "text", "content": "ok", "usage": {
            "input_tokens": in_tok, "output_tokens": out_tok,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
        }}

    def test_cache_fields_appended(self):
        from executors.plugins.tool_loop_v1 import _acc_usage
        target = []
        _acc_usage(target, self._make_result(10, 5, cache_read=8, cache_creation=2))
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0]["cache_read_tokens"], 8)
        self.assertEqual(target[0]["cache_creation_tokens"], 2)

    def test_zero_cache_still_appended(self):
        from executors.plugins.tool_loop_v1 import _acc_usage
        target = []
        _acc_usage(target, self._make_result(10, 5, cache_read=0, cache_creation=0))
        self.assertEqual(target[0]["cache_read_tokens"], 0)
        self.assertEqual(target[0]["cache_creation_tokens"], 0)

    def test_missing_cache_fields_default_to_zero(self):
        from executors.plugins.tool_loop_v1 import _acc_usage
        target = []
        # usage dict has no cache keys at all (older code path)
        _acc_usage(target, {"type": "text", "content": "ok",
                            "usage": {"input_tokens": 5, "output_tokens": 2}})
        self.assertEqual(target[0]["cache_read_tokens"], 0)
        self.assertEqual(target[0]["cache_creation_tokens"], 0)

    def test_multiple_accumulations_sum_correctly(self):
        from executors.plugins.tool_loop_v1 import _acc_usage
        target = []
        _acc_usage(target, self._make_result(10, 4, cache_read=8, cache_creation=1))
        _acc_usage(target, self._make_result(20, 6, cache_read=15, cache_creation=3))
        total_read = sum(u["cache_read_tokens"] for u in target)
        total_creation = sum(u["cache_creation_tokens"] for u in target)
        self.assertEqual(total_read, 23)
        self.assertEqual(total_creation, 4)


# =============================================================================
# 8. save_message — SQL includes cache columns
# =============================================================================

class TestSaveMessageCacheColumns(unittest.IsolatedAsyncioTestCase):

    def _make_save_db(self):
        """save_message uses 'async with db.execute(...)', so mock must be a sync callable
        returning an async context manager."""
        mock_cur = MagicMock()
        mock_cur.lastrowid = 1
        execute_cm = MagicMock()
        execute_cm.__aenter__ = AsyncMock(return_value=mock_cur)
        execute_cm.__aexit__ = AsyncMock(return_value=False)
        mock_db = MagicMock()
        mock_db.execute = MagicMock(return_value=execute_cm)
        mock_db.commit = AsyncMock()
        return mock_db

    async def test_cache_fields_in_insert_sql(self):
        from db.queries import save_message

        mock_db = self._make_save_db()
        await save_message(
            mock_db, group_id=1, member_id=2, content="hi",
            input_tokens=10, output_tokens=5,
            cache_read_tokens=8, cache_creation_tokens=2,
        )

        sql, params = mock_db.execute.call_args[0]
        self.assertIn("cache_read_tokens", sql)
        self.assertIn("cache_creation_tokens", sql)
        self.assertIn(8, params)
        self.assertIn(2, params)

    async def test_none_cache_fields_passed_as_none(self):
        from db.queries import save_message

        mock_db = self._make_save_db()
        await save_message(mock_db, group_id=1, member_id=2, content="hi")
        _, params = mock_db.execute.call_args[0]
        # cache_read_tokens and cache_creation_tokens are the last two positional params
        self.assertIsNone(params[-2])
        self.assertIsNone(params[-1])


# =============================================================================
# 9. update_message — COALESCE for cache columns
# =============================================================================

class TestUpdateMessageCacheColumns(unittest.IsolatedAsyncioTestCase):

    async def test_cache_fields_written_when_provided(self):
        from db.queries import update_message

        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        await update_message(mock_db, msg_id=5, content="new",
                             input_tokens=10, output_tokens=4,
                             cache_read_tokens=7, cache_creation_tokens=1)

        sql, params = mock_db.execute.call_args[0]
        self.assertIn("cache_read_tokens", sql)
        self.assertIn("cache_creation_tokens", sql)
        self.assertIn("COALESCE", sql)
        self.assertEqual(params, ("new", 10, 4, 7, 1, 5))

    async def test_none_cache_fields_preserve_existing(self):
        from db.queries import update_message

        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        await update_message(mock_db, msg_id=3, content="edited")
        _, params = mock_db.execute.call_args[0]
        # params: (content, input_tokens, output_tokens, cache_read, cache_creation, msg_id)
        self.assertEqual(params[0], "edited")
        self.assertIsNone(params[3])   # cache_read_tokens
        self.assertIsNone(params[4])   # cache_creation_tokens
        self.assertEqual(params[5], 3)


# =============================================================================
# 10. _row_to_msg — maps cache columns from SELECT result
# =============================================================================

class TestRowToMsgCacheFields(unittest.TestCase):

    def _make_row(self, cache_read=None, cache_creation=None,
                  provider="deepseek", model="deepseek-chat"):
        # Matches _MSG_SQL column order: 24 columns total
        return (
            1,           # id
            1,           # group_id
            2,           # member_id
            "content",   # content
            "2026-01-01T00:00:00Z",  # created_at
            "Bot",       # mb.name
            "bot",       # mb.type
            "#fff",      # mb.avatar_color
            None,        # reply_to_id
            None,        # rm.content
            None,        # rmb.name
            None,        # edited_at
            0,           # is_deleted
            None,        # file_url
            None,        # file_name
            None,        # file_size
            None,        # file_type
            0,           # is_auto_reply
            20,          # input_tokens   (r[18])
            8,           # output_tokens  (r[19])
            cache_read,          # cache_read_tokens (r[20])
            cache_creation,      # cache_creation_tokens (r[21])
            provider,    # mb.model_provider (r[22])
            model,       # mb.model_name     (r[23])
        )

    def test_cache_fields_mapped(self):
        from db.models import _row_to_msg
        row = self._make_row(cache_read=150, cache_creation=30)
        msg = _row_to_msg(row)
        self.assertEqual(msg["cache_read_tokens"], 150)
        self.assertEqual(msg["cache_creation_tokens"], 30)

    def test_null_cache_fields_returned_as_none(self):
        from db.models import _row_to_msg
        row = self._make_row(cache_read=None, cache_creation=None)
        msg = _row_to_msg(row)
        self.assertIsNone(msg["cache_read_tokens"])
        self.assertIsNone(msg["cache_creation_tokens"])

    def test_cost_usd_computed_for_deepseek(self):
        from db.models import _row_to_msg
        from ai.pricing import calculate_cost
        row = self._make_row(cache_read=5, cache_creation=None,
                             provider="deepseek", model="deepseek-chat")
        msg = _row_to_msg(row)
        expected = calculate_cost("deepseek", "deepseek-chat", {
            "input_tokens": 20, "output_tokens": 8,
            "cache_read_tokens": 5, "cache_creation_tokens": None,
        })
        self.assertAlmostEqual(msg["cost_usd"], expected, places=12)
        self.assertGreater(msg["cost_usd"], 0)

    def test_cost_usd_none_when_no_tokens(self):
        from db.models import _row_to_msg
        # zero/None tokens → no cost computed
        row = list(self._make_row(cache_read=None, cache_creation=None))
        row[18] = None  # input_tokens
        row[19] = None  # output_tokens
        msg = _row_to_msg(tuple(row))
        self.assertIsNone(msg["cost_usd"])


# =============================================================================
# 11. simple_v1 — cache tokens propagated to save_message
# =============================================================================

class TestSimpleV1CacheTokens(unittest.IsolatedAsyncioTestCase):

    async def test_cache_tokens_saved(self):
        from executors.plugins import simple_v1
        from executors.base import ExecutionContext

        bot = {
            "id": 1, "name": "Bot", "type": "bot", "role": "助手",
            "system_prompt": "sys", "personality_prompt": None,
            "avatar_color": "#fff",
            "model_provider": "deepseek", "model_name": "deepseek-chat",
            "temperature": 0.7, "max_tokens": 4096,
        }
        saved_kwargs = {}

        async def fake_stream(sp, hist, msg, prov, model, temp, max_tok, usage_out=None):
            if usage_out is not None:
                usage_out.append({
                    "input_tokens": 15, "output_tokens": 6,
                    "cache_read_tokens": 12, "cache_creation_tokens": 0,
                })
            yield "hello"

        async def fake_save(db, group_id, member_id, content, **kwargs):
            saved_kwargs.update(kwargs)
            return 10

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        ctx = ExecutionContext(
            bot=bot, group_id=1, user_message="hi",
            sender={"id": 99, "name": "User", "type": "human"},
            history=[], all_bots=[bot], all_members=[bot],
            broadcaster=MagicMock(),
        )
        ctx.broadcaster.broadcast = AsyncMock()

        executor = simple_v1.SimpleV1()
        with patch.object(simple_v1, "call_ai_stream", new=fake_stream), \
             patch.object(simple_v1, "save_message", new=fake_save), \
             patch.object(simple_v1, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(simple_v1, "get_db", return_value=fake_db_cm), \
             patch.object(simple_v1, "get_memory_context", new=AsyncMock(return_value="")), \
             patch.object(simple_v1, "add_to_chroma", new=AsyncMock()), \
             patch.object(simple_v1, "maybe_summarize", new=AsyncMock()), \
             patch.object(simple_v1, "append_log", new=AsyncMock()):
            await executor.run(ctx)

        self.assertEqual(saved_kwargs.get("cache_read_tokens"), 12)
        self.assertIsNone(saved_kwargs.get("cache_creation_tokens"))  # 0 → None via `or None`


# =============================================================================
# 12. react_v1 — cache tokens accumulated and saved
# =============================================================================

class TestReactV1CacheTokens(unittest.IsolatedAsyncioTestCase):

    async def test_cache_tokens_accumulated_and_saved(self):
        from executors.plugins import react_v1
        from executors.base import ExecutionContext

        bot = {
            "id": 1, "name": "ReactBot", "type": "bot", "role": "推理",
            "system_prompt": "think", "personality_prompt": None,
            "avatar_color": "#abc",
            "model_provider": "deepseek", "model_name": "deepseek-chat",
            "temperature": 0.7, "max_tokens": 4096,
            "executor_config": {},
        }
        saved_kwargs = {}
        call_count = 0

        async def fake_call_ai_once(sp, msgs, prov, model, temp, max_tok, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: tool call iteration with cache tokens
                return {
                    "type": "text",
                    "content": "Final Answer: done",
                    "usage": {
                        "input_tokens": 20, "output_tokens": 8,
                        "cache_read_tokens": 15, "cache_creation_tokens": 3,
                    },
                }
            return {"type": "text", "content": "done",
                    "usage": {"input_tokens": 5, "output_tokens": 2,
                              "cache_read_tokens": 4, "cache_creation_tokens": 0}}

        async def fake_save(db, group_id, member_id, content, **kwargs):
            saved_kwargs.update(kwargs)
            return 20

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        ctx = ExecutionContext(
            bot=bot, group_id=1, user_message="think about this",
            sender={"id": 99, "name": "User", "type": "human"},
            history=[], all_bots=[bot], all_members=[bot],
            broadcaster=MagicMock(),
        )
        ctx.broadcaster.broadcast = AsyncMock()

        executor = react_v1.ReactV1()
        with patch.object(react_v1, "call_ai_once", new=fake_call_ai_once), \
             patch.object(react_v1, "save_message", new=fake_save), \
             patch.object(react_v1, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(react_v1, "get_db", return_value=fake_db_cm), \
             patch.object(react_v1, "get_memory_context", new=AsyncMock(return_value="")), \
             patch.object(react_v1, "add_to_chroma", new=AsyncMock()), \
             patch.object(react_v1, "maybe_summarize", new=AsyncMock()), \
             patch.object(react_v1, "append_log", new=AsyncMock()):
            await executor.run(ctx)

        self.assertEqual(saved_kwargs.get("cache_read_tokens"), 15)
        self.assertEqual(saved_kwargs.get("cache_creation_tokens"), 3)


if __name__ == "__main__":
    unittest.main()
