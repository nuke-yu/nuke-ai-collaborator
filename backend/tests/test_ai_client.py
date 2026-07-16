import unittest
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.client import _to_claude_messages

class TestClaudeMessageFormatting(unittest.TestCase):

    def test_basic_conversion(self):
        """Test user and assistant message conversion."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"}
        ]
        expected = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"}
        ]
        self.assertEqual(_to_claude_messages(messages), expected)

    def test_consecutive_same_role_messages(self):
        """Test that consecutive messages with the same role are merged."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "world"},
            {"role": "assistant", "content": "how can I help?"},
            {"role": "assistant", "content": "today?"}
        ]
        # Merged result should have text blocks concatenated
        result = _to_claude_messages(messages)
        self.assertEqual(len(result), 2)
        
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(
            result[0]["content"],
            [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
        )
        
        self.assertEqual(result[1]["role"], "assistant")
        self.assertEqual(
            result[1]["content"],
            [{"type": "text", "text": "how can I help?"}, {"type": "text", "text": "today?"}]
        )

    def test_tool_use_and_tool_result_grouping(self):
        """Test tool calls and merging consecutive tool results."""
        messages = [
            {"role": "user", "content": "run the script"},
            {
                "role": "assistant",
                "content": "Sure, running it.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": '{"cmd": "echo 1"}'
                        }
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": '{"cmd": "echo 2"}'
                        }
                    }
                ]
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "output 1"
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "content": "output 2"
            }
        ]
        
        result = _to_claude_messages(messages)
        
        # We expect:
        # 1. user: "run the script"
        # 2. assistant: text "Sure, running it." + two "tool_use" blocks
        # 3. user: two "tool_result" blocks (grouped)
        self.assertEqual(len(result), 3)
        
        # Check user
        self.assertEqual(result[0], {"role": "user", "content": "run the script"})
        
        # Check assistant tool use
        self.assertEqual(result[1]["role"], "assistant")
        self.assertEqual(len(result[1]["content"]), 3)
        self.assertEqual(result[1]["content"][0], {"type": "text", "text": "Sure, running it."})
        self.assertEqual(result[1]["content"][1], {
            "type": "tool_use",
            "id": "call_1",
            "name": "run_shell",
            "input": {"cmd": "echo 1"}
        })
        self.assertEqual(result[1]["content"][2], {
            "type": "tool_use",
            "id": "call_2",
            "name": "run_shell",
            "input": {"cmd": "echo 2"}
        })
        
        # Check tool results grouped inside a single user message
        self.assertEqual(result[2]["role"], "user")
        self.assertEqual(len(result[2]["content"]), 2)
        self.assertEqual(result[2]["content"][0], {
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "output 1"
        })
        self.assertEqual(result[2]["content"][1], {
            "type": "tool_result",
            "tool_use_id": "call_2",
            "content": "output 2"
        })

    def test_strip_system_messages(self):
        """Test that system messages are removed since system prompt is passed separately in Claude API."""
        messages = [
            {"role": "system", "content": "you are a helper"},
            {"role": "user", "content": "hello"}
        ]
        expected = [
            {"role": "user", "content": "hello"}
        ]
        self.assertEqual(_to_claude_messages(messages), expected)


# ---------------------------------------------------------------------------
# Tests for use_cached_microcompact (Strategy 5)
# ---------------------------------------------------------------------------

class TestCachedMicrocompact(unittest.IsolatedAsyncioTestCase):
    """Verify _once_claude injects the correct header/body for Strategy 5,
    and call_ai_once threads the parameter through."""

    def _make_mock_response(self, content_text="ok"):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": content_text}]
        }
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def _make_mock_client(self, mock_resp):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm, mock_client

    async def test_once_claude_adds_headers_and_body_when_enabled(self):
        from ai.client import _once_claude
        mock_resp = self._make_mock_response()
        mock_cm, mock_client = self._make_mock_client(mock_resp)

        with patch("ai.client._get_client", return_value=mock_client):
            await _once_claude(
                api_key="test-key",
                model="claude-opus-4-7",
                system_prompt="sp",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.7,
                max_tokens=1024,
                tools=None,
                use_cached_microcompact=True,
            )

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs["headers"]
        body = call_kwargs.kwargs["json"]
        self.assertEqual(headers.get("anthropic-beta"), "context-management-2025-09-19")
        self.assertIn("context_management", body)
        self.assertEqual(body["context_management"], {"type": "clear_tool_uses_20250919"})

    async def test_once_claude_no_headers_when_disabled(self):
        from ai.client import _once_claude
        mock_resp = self._make_mock_response()
        mock_cm, mock_client = self._make_mock_client(mock_resp)

        with patch("ai.client._get_client", return_value=mock_client):
            await _once_claude(
                api_key="test-key",
                model="claude-opus-4-7",
                system_prompt="sp",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.7,
                max_tokens=1024,
                tools=None,
                use_cached_microcompact=False,
            )

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs["headers"]
        body = call_kwargs.kwargs["json"]
        self.assertNotIn("anthropic-beta", headers)
        self.assertNotIn("context_management", body)

    async def test_once_claude_default_is_disabled(self):
        from ai.client import _once_claude
        mock_resp = self._make_mock_response()
        mock_cm, mock_client = self._make_mock_client(mock_resp)

        with patch("ai.client._get_client", return_value=mock_client):
            await _once_claude(
                api_key="test-key",
                model="claude-opus-4-7",
                system_prompt="sp",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.7,
                max_tokens=1024,
                tools=None,
                # use_cached_microcompact not passed → default False
            )

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs["headers"]
        self.assertNotIn("anthropic-beta", headers)

    async def test_call_ai_once_threads_cached_mc_to_claude(self):
        """call_ai_once with provider='claude' must pass use_cached_microcompact to _once_claude."""
        from ai.client import call_ai_once
        captured = {}

        async def fake_once_claude(api_key, model, system_prompt, messages,
                                   temperature, max_tokens, tools,
                                   use_cached_microcompact=False):
            captured["use_cached_microcompact"] = use_cached_microcompact
            return {"type": "text", "content": "ok"}

        with patch("ai.client._require_key", return_value="fake-key"), \
             patch("ai.client._once_claude", new=fake_once_claude):
            await call_ai_once(
                "sp", [{"role": "user", "content": "hi"}],
                provider="claude", model="claude-opus-4-7",
                use_cached_microcompact=True,
            )

        self.assertTrue(captured.get("use_cached_microcompact"))

    async def test_call_ai_once_non_claude_ignores_cached_mc(self):
        """Non-Claude providers should not attempt to pass use_cached_microcompact."""
        from ai.client import call_ai_once
        captured = {}

        async def fake_once_openai(url, api_key, model, system_prompt,
                                   messages, temperature, max_tokens, tools):
            captured["called"] = True
            return {"type": "text", "content": "ok"}

        with patch("ai.client._require_key", return_value="fake-key"), \
             patch("ai.client._once_openai_compat", new=fake_once_openai):
            await call_ai_once(
                "sp", [{"role": "user", "content": "hi"}],
                provider="deepseek", model="deepseek-chat",
                use_cached_microcompact=True,  # passed but should be ignored for non-claude
            )

        self.assertTrue(captured.get("called"))


class TestRetryAndRateLimit(unittest.IsolatedAsyncioTestCase):

    def test_parse_retry_after_ms(self):
        from ai.client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after-ms": "5000"}), 5.0)

    def test_parse_retry_after_seconds(self):
        from ai.client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after": "3"}), 3.0)

    def test_parse_retry_after_ms_takes_priority(self):
        from ai.client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after-ms": "2000", "retry-after": "10"}), 2.0)

    def test_parse_retry_after_no_header_returns_default(self):
        from ai.client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({}), 2.0)

    def test_parse_retry_after_invalid_value_returns_default(self):
        from ai.client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after": "not-a-number"}), 2.0)

    def test_parse_retry_after_titlecase_header(self):
        from ai.client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"Retry-After": "5"}), 5.0)

    async def test_call_ai_once_retries_on_rate_limit_and_succeeds(self):
        from ai.client import call_ai_once
        call_count = 0

        async def fake_dispatch(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                from ai.client import AIRateLimitError
                raise AIRateLimitError(0.001)  # tiny wait for test speed
            return {"type": "text", "content": "ok"}

        with patch("ai.client._dispatch_once", new=fake_dispatch), \
             patch("ai.client._keys", return_value={}), \
             patch("ai.client.asyncio.sleep", new=AsyncMock()):
            result = await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                        provider="deepseek", model="deepseek-chat")

        self.assertEqual(result["content"], "ok")
        self.assertEqual(call_count, 2)

    async def test_call_ai_once_exhausted_raises_ai_error(self):
        from ai.client import call_ai_once, AIError, AIRateLimitError

        async def always_rate_limit(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            raise AIRateLimitError(0.001)

        with patch("ai.client._dispatch_once", new=always_rate_limit), \
             patch("ai.client._keys", return_value={}), \
             patch("ai.client.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(AIError):
                await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                   provider="deepseek", model="deepseek-chat")

    async def test_call_ai_once_uses_fallback_model_after_rate_limit(self):
        from ai.client import call_ai_once, AIRateLimitError
        tried_models = []

        async def fake_dispatch(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            tried_models.append(model)
            if model == "main-model":
                raise AIRateLimitError(0.001)
            return {"type": "text", "content": "fallback ok"}

        with patch("ai.client._dispatch_once", new=fake_dispatch), \
             patch("ai.client._keys", return_value={}), \
             patch("ai.client.asyncio.sleep", new=AsyncMock()):
            result = await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                        provider="deepseek", model="main-model",
                                        fallback_model="fallback-model")

        self.assertIn("fallback-model", tried_models)
        self.assertEqual(result["content"], "fallback ok")


# ---------------------------------------------------------------------------
# Tests for token usage extraction (_once_openai_compat, _once_claude)
# ---------------------------------------------------------------------------

class TestTokenUsageExtraction(unittest.IsolatedAsyncioTestCase):
    """Verify that _once_openai_compat and _once_claude return a 'usage' dict."""

    # ── openai-compat ────────────────────────────────────────────────────────

    def _make_openai_resp(self, content="hi", prompt_tokens=10, completion_tokens=5,
                          tool_calls=None, finish_reason=None):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        msg = {"content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        mock_resp.json.return_value = {
            "choices": [{"message": msg, "finish_reason": finish_reason or "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }
        return mock_resp

    async def test_openai_text_response_includes_usage(self):
        from ai.client import _once_openai_compat
        mock_resp = self._make_openai_resp(prompt_tokens=20, completion_tokens=8)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("ai.client._get_client", return_value=mock_client):
            result = await _once_openai_compat(
                url="https://api.deepseek.com/v1/chat/completions",
                api_key="key", model="deepseek-chat",
                system_prompt="sp", messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=256, tools=None,
            )

        self.assertEqual(result["type"], "text")
        self.assertIn("usage", result)
        self.assertEqual(result["usage"]["input_tokens"], 20)
        self.assertEqual(result["usage"]["output_tokens"], 8)

    async def test_openai_tool_calls_response_includes_usage(self):
        from ai.client import _once_openai_compat
        tool_calls = [{
            "id": "c1", "type": "function",
            "function": {"name": "run_shell", "arguments": '{"cmd": "ls"}'},
        }]
        mock_resp = self._make_openai_resp(
            prompt_tokens=30, completion_tokens=15,
            tool_calls=tool_calls, finish_reason="tool_calls",
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("ai.client._get_client", return_value=mock_client):
            result = await _once_openai_compat(
                url="https://api.deepseek.com/v1/chat/completions",
                api_key="key", model="deepseek-chat",
                system_prompt="sp", messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=256, tools=None,
            )

        self.assertEqual(result["type"], "tool_calls")
        self.assertIn("usage", result)
        self.assertEqual(result["usage"]["input_tokens"], 30)
        self.assertEqual(result["usage"]["output_tokens"], 15)

    async def test_malformed_tool_arguments_are_transient(self):
        from ai.client import LLMTransientError, _once_openai_compat
        tool_calls = [{
            "id": "c1", "type": "function",
            "function": {"name": "write_file", "arguments": "not-json"},
        }]
        mock_resp = self._make_openai_resp(
            tool_calls=tool_calls, finish_reason="tool_calls",
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("ai.client._get_client", return_value=mock_client):
            with self.assertRaisesRegex(LLMTransientError, "write_file"):
                await _once_openai_compat(
                    url="https://api.deepseek.com/v1/chat/completions",
                    api_key="key", model="deepseek-chat",
                    system_prompt="sp", messages=[{"role": "user", "content": "hi"}],
                    temperature=0.7, max_tokens=256, tools=None,
                )

    async def test_openai_missing_usage_field_returns_zeros(self):
        """If the API omits 'usage', input_tokens and output_tokens default to 0."""
        from ai.client import _once_openai_compat
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            # no 'usage' key
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("ai.client._get_client", return_value=mock_client):
            result = await _once_openai_compat(
                url="https://api.deepseek.com/v1/chat/completions",
                api_key="key", model="deepseek-chat",
                system_prompt="sp", messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=256, tools=None,
            )

        self.assertEqual(result["usage"]["input_tokens"], 0)
        self.assertEqual(result["usage"]["output_tokens"], 0)

    # ── claude ───────────────────────────────────────────────────────────────

    def _make_claude_resp(self, text="hello", input_tokens=12, output_tokens=7,
                          tool_use_blocks=None):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        content = [{"type": "text", "text": text}]
        if tool_use_blocks:
            content.extend(tool_use_blocks)
        mock_resp.json.return_value = {
            "content": content,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
        return mock_resp

    async def test_claude_text_response_includes_usage(self):
        from ai.client import _once_claude
        mock_resp = self._make_claude_resp(input_tokens=12, output_tokens=7)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("ai.client._get_client", return_value=mock_client):
            result = await _once_claude(
                api_key="test-key", model="claude-opus-4-7",
                system_prompt="sp", messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=256, tools=None,
            )

        self.assertEqual(result["type"], "text")
        self.assertIn("usage", result)
        self.assertEqual(result["usage"]["input_tokens"], 12)
        self.assertEqual(result["usage"]["output_tokens"], 7)

    async def test_claude_tool_calls_response_includes_usage(self):
        from ai.client import _once_claude
        mock_resp = self._make_claude_resp(
            input_tokens=25, output_tokens=10,
            tool_use_blocks=[{
                "type": "tool_use", "id": "tu1",
                "name": "run_shell", "input": {"cmd": "pwd"},
            }],
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("ai.client._get_client", return_value=mock_client):
            result = await _once_claude(
                api_key="test-key", model="claude-opus-4-7",
                system_prompt="sp", messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=256, tools=None,
            )

        self.assertEqual(result["type"], "tool_calls")
        self.assertIn("usage", result)
        self.assertEqual(result["usage"]["input_tokens"], 25)
        self.assertEqual(result["usage"]["output_tokens"], 10)

    async def test_claude_missing_usage_returns_zeros(self):
        from ai.client import _once_claude
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "ok"}],
            # no 'usage' key
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("ai.client._get_client", return_value=mock_client):
            result = await _once_claude(
                api_key="test-key", model="claude-opus-4-7",
                system_prompt="sp", messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=256, tools=None,
            )

        self.assertEqual(result["usage"]["input_tokens"], 0)
        self.assertEqual(result["usage"]["output_tokens"], 0)


# ---------------------------------------------------------------------------
# Tests for streaming usage capture (_stream_openai_compat, _stream_claude)
# ---------------------------------------------------------------------------

class TestStreamingUsageCapture(unittest.IsolatedAsyncioTestCase):
    """Verify that _stream_openai_compat and _stream_claude populate usage_out."""

    def _make_stream_mock(self, sse_lines: list):
        """Build mock httpx streaming plumbing from a list of raw SSE line strings."""
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

        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=mock_client)
        client_cm.__aexit__ = AsyncMock(return_value=False)

        return client_cm, mock_client

    # ── OpenAI-compat streaming ──────────────────────────────────────────────

    async def test_stream_openai_usage_out_populated(self):
        """Final SSE chunk with usage field populates usage_out correctly."""
        from ai.client import _stream_openai_compat
        lines = [
            f"data: {json.dumps({'choices': [{'delta': {'content': 'hello '}}]})}",
            f"data: {json.dumps({'choices': [{'delta': {'content': 'world'}}]})}",
            f"data: {json.dumps({'choices': [{'delta': {}}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 8}})}",
            "data: [DONE]",
        ]
        client_cm, mock_client = self._make_stream_mock(lines)
        usage_out: list = []

        with patch("ai.client._get_client", return_value=mock_client):
            chunks = []
            async for chunk in _stream_openai_compat(
                url="https://api.deepseek.com/v1/chat/completions",
                api_key="key", model="deepseek-chat",
                messages=[{"role": "user", "content": "hi"}],
                usage_out=usage_out,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks, ["hello ", "world"])
        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["input_tokens"], 20)
        self.assertEqual(usage_out[0]["output_tokens"], 8)

    async def test_stream_openai_no_usage_chunk_leaves_usage_out_empty(self):
        """If no usage chunk arrives, usage_out stays empty."""
        from ai.client import _stream_openai_compat
        lines = [
            f"data: {json.dumps({'choices': [{'delta': {'content': 'hi'}}]})}",
            "data: [DONE]",
        ]
        client_cm, mock_client = self._make_stream_mock(lines)
        usage_out: list = []

        with patch("ai.client._get_client", return_value=mock_client):
            chunks = []
            async for chunk in _stream_openai_compat(
                url="https://api.deepseek.com/v1/chat/completions",
                api_key="key", model="deepseek-chat",
                messages=[{"role": "user", "content": "hi"}],
                usage_out=usage_out,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks, ["hi"])
        self.assertEqual(usage_out, [])

    async def test_stream_openai_usage_out_none_omits_stream_options(self):
        """When usage_out=None, stream_options must NOT be in the request body."""
        from ai.client import _stream_openai_compat
        client_cm, mock_client = self._make_stream_mock(["data: [DONE]"])

        with patch("ai.client._get_client", return_value=mock_client):
            async for _ in _stream_openai_compat(
                url="https://api.deepseek.com/v1/chat/completions",
                api_key="key", model="deepseek-chat",
                messages=[{"role": "user", "content": "hi"}],
                usage_out=None,
            ):
                pass

        body = mock_client.stream.call_args.kwargs["json"]
        self.assertNotIn("stream_options", body)

    async def test_stream_openai_usage_out_set_adds_stream_options(self):
        """When usage_out is a list, stream_options must be added to the request body."""
        from ai.client import _stream_openai_compat
        client_cm, mock_client = self._make_stream_mock(["data: [DONE]"])

        with patch("ai.client._get_client", return_value=mock_client):
            async for _ in _stream_openai_compat(
                url="https://api.deepseek.com/v1/chat/completions",
                api_key="key", model="deepseek-chat",
                messages=[{"role": "user", "content": "hi"}],
                usage_out=[],
            ):
                pass

        body = mock_client.stream.call_args.kwargs["json"]
        self.assertIn("stream_options", body)
        self.assertEqual(body["stream_options"], {"include_usage": True})

    # ── Claude streaming ─────────────────────────────────────────────────────

    async def test_stream_claude_usage_out_populated(self):
        """message_start + message_delta events populate usage_out correctly."""
        from ai.client import _stream_claude
        lines = [
            f"data: {json.dumps({'type': 'message_start', 'message': {'usage': {'input_tokens': 12}}})}",
            f"data: {json.dumps({'type': 'content_block_delta', 'delta': {'text': 'hi there'}})}",
            f"data: {json.dumps({'type': 'message_delta', 'usage': {'output_tokens': 7}})}",
        ]
        client_cm, mock_client = self._make_stream_mock(lines)
        usage_out: list = []

        with patch("ai.client._get_client", return_value=mock_client):
            chunks = []
            async for chunk in _stream_claude(
                model="claude-sonnet-4-6",
                system_prompt="sp",
                messages=[{"role": "user", "content": "hi"}],
                api_key="test-key",
                usage_out=usage_out,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks, ["hi there"])
        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["input_tokens"], 12)
        self.assertEqual(usage_out[0]["output_tokens"], 7)

    async def test_stream_claude_usage_out_none_still_streams_content(self):
        """When usage_out=None, usage events are silently ignored and content still yields."""
        from ai.client import _stream_claude
        lines = [
            f"data: {json.dumps({'type': 'message_start', 'message': {'usage': {'input_tokens': 12}}})}",
            f"data: {json.dumps({'type': 'content_block_delta', 'delta': {'text': 'hello'}})}",
            f"data: {json.dumps({'type': 'message_delta', 'usage': {'output_tokens': 5}})}",
        ]
        client_cm, mock_client = self._make_stream_mock(lines)

        with patch("ai.client._get_client", return_value=mock_client):
            chunks = []
            async for chunk in _stream_claude(
                model="claude-sonnet-4-6",
                system_prompt="sp",
                messages=[{"role": "user", "content": "hi"}],
                api_key="test-key",
                usage_out=None,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks, ["hello"])

    async def test_stream_claude_message_delta_without_prior_start(self):
        """message_delta without a preceding message_start initializes usage_out safely."""
        from ai.client import _stream_claude
        lines = [
            f"data: {json.dumps({'type': 'message_delta', 'usage': {'output_tokens': 5}})}",
        ]
        client_cm, mock_client = self._make_stream_mock(lines)
        usage_out: list = []

        with patch("ai.client._get_client", return_value=mock_client):
            async for _ in _stream_claude(
                model="claude-sonnet-4-6",
                system_prompt="sp",
                messages=[{"role": "user", "content": "hi"}],
                api_key="test-key",
                usage_out=usage_out,
            ):
                pass

        self.assertEqual(len(usage_out), 1)
        self.assertEqual(usage_out[0]["input_tokens"], 0)
        self.assertEqual(usage_out[0]["output_tokens"], 5)


class TestDsmlToolCallRecovery(unittest.TestCase):
    """deepseek-chat 间歇性把工具调用以 DSML 文本塞进 content（而非结构化 tool_calls
    字段），导致调用不执行、乱码存进消息。_recover_dsml_tool_calls 把这种泄漏救回成
    真正的 tool_calls，并产出干净的 assistant_message（无 DSML 文本）以防上下文污染。"""

    # 真实泄漏样本：全角竖线 ｜(U+FF5C) 包裹的 DSML 标记
    P = "｜｜DSML｜｜"

    def _wrap(self, body):
        return f"<{self.P}tool_calls>\n{body}\n</{self.P}tool_calls>"

    def test_recovers_leaked_write_file_call(self):
        from ai.client import _recover_dsml_tool_calls
        content = (
            "好的，直接开干！\n\n"
            + self._wrap(
                f'<{self.P}invoke name="write_file">\n'
                f'<{self.P}parameter name="path" string="true">index.html</{self.P}parameter>\n'
                f'<{self.P}parameter name="content" string="true"><h1>hi</h1></{self.P}parameter>\n'
                f'</{self.P}invoke>'
            )
        )
        usage = {"input_tokens": 1, "output_tokens": 2,
                 "cache_read_tokens": 0, "cache_creation_tokens": 0}
        res = _recover_dsml_tool_calls(content, usage)
        self.assertIsNotNone(res)
        self.assertEqual(res["type"], "tool_calls")
        self.assertEqual(len(res["calls"]), 1)
        call = res["calls"][0]
        self.assertEqual(call["name"], "write_file")
        self.assertEqual(call["arguments"]["path"], "index.html")
        self.assertEqual(call["arguments"]["content"], "<h1>hi</h1>")
        self.assertEqual(res["usage"], usage)
        # assistant_message 必须是干净的 OpenAI tool_calls（无 DSML 文本）防污染
        am = res["assistant_message"]
        self.assertIsNone(am.get("content"))
        self.assertEqual(am["tool_calls"][0]["function"]["name"], "write_file")
        self.assertNotIn("DSML", json.dumps(am, ensure_ascii=False))
        # arguments 在 assistant_message 里是 JSON 字符串（OpenAI 格式）
        self.assertEqual(
            json.loads(am["tool_calls"][0]["function"]["arguments"])["path"], "index.html")

    def test_multiple_invokes_in_order(self):
        from ai.client import _recover_dsml_tool_calls
        content = (
            f'<{self.P}invoke name="list_workspace">\n'
            f'<{self.P}parameter name="path" string="true">.</{self.P}parameter>\n'
            f'</{self.P}invoke>\n'
            f'<{self.P}invoke name="read_file">\n'
            f'<{self.P}parameter name="path" string="true">a.txt</{self.P}parameter>\n'
            f'</{self.P}invoke>'
        )
        res = _recover_dsml_tool_calls(content, {})
        self.assertIsNotNone(res)
        self.assertEqual([c["name"] for c in res["calls"]], ["list_workspace", "read_file"])
        ids = [c["id"] for c in res["calls"]]
        self.assertEqual(len(set(ids)), 2)  # 每个调用 id 唯一

    def test_plain_text_returns_none(self):
        from ai.client import _recover_dsml_tool_calls
        self.assertIsNone(_recover_dsml_tool_calls("就是一段普通回答，没有工具调用", {}))
        self.assertIsNone(_recover_dsml_tool_calls("", {}))
        self.assertIsNone(_recover_dsml_tool_calls(None, {}))

    def test_invoke_without_params(self):
        from ai.client import _recover_dsml_tool_calls
        content = f'<{self.P}invoke name="list_workspace"></{self.P}invoke>'
        res = _recover_dsml_tool_calls(content, {})
        self.assertIsNotNone(res)
        self.assertEqual(res["calls"][0]["name"], "list_workspace")
        self.assertEqual(res["calls"][0]["arguments"], {})


class TestRepairJsonArguments(unittest.TestCase):
    def test_normal_json(self):
        from ai.client import repair_json_arguments
        self.assertEqual(repair_json_arguments('{"path": "app.js", "content": "ok"}'), {"path": "app.js", "content": "ok"})

    def test_truncated_json_simple(self):
        from ai.client import repair_json_arguments
        self.assertEqual(repair_json_arguments('{"path": "app.js", "content": "ok'), {"path": "app.js", "content": "ok"})

    def test_truncated_json_escaped(self):
        from ai.client import repair_json_arguments
        self.assertEqual(repair_json_arguments('{"path": "app.js", "content": "console.log(\\"hello\\");'), {"path": "app.js", "content": 'console.log("hello");'})

    def test_truncated_json_backslash_end(self):
        from ai.client import repair_json_arguments
        self.assertEqual(repair_json_arguments('{"path": "app.js", "content": "console.log(\\"hello\\");\\'), {"path": "app.js", "content": 'console.log("hello");'})

    def test_invalid_json(self):
        from ai.client import repair_json_arguments
        self.assertIsNone(repair_json_arguments('invalid'))


if __name__ == "__main__":
    unittest.main()
