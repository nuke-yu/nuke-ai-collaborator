import unittest
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_client import _to_claude_messages

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
        from ai_client import _once_claude
        mock_resp = self._make_mock_response()
        mock_cm, mock_client = self._make_mock_client(mock_resp)

        with patch("ai_client.httpx.AsyncClient", return_value=mock_cm):
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
        from ai_client import _once_claude
        mock_resp = self._make_mock_response()
        mock_cm, mock_client = self._make_mock_client(mock_resp)

        with patch("ai_client.httpx.AsyncClient", return_value=mock_cm):
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
        from ai_client import _once_claude
        mock_resp = self._make_mock_response()
        mock_cm, mock_client = self._make_mock_client(mock_resp)

        with patch("ai_client.httpx.AsyncClient", return_value=mock_cm):
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
        from ai_client import call_ai_once
        captured = {}

        async def fake_once_claude(api_key, model, system_prompt, messages,
                                   temperature, max_tokens, tools,
                                   use_cached_microcompact=False):
            captured["use_cached_microcompact"] = use_cached_microcompact
            return {"type": "text", "content": "ok"}

        with patch("ai_client._require_key", return_value="fake-key"), \
             patch("ai_client._once_claude", new=fake_once_claude):
            await call_ai_once(
                "sp", [{"role": "user", "content": "hi"}],
                provider="claude", model="claude-opus-4-7",
                use_cached_microcompact=True,
            )

        self.assertTrue(captured.get("use_cached_microcompact"))

    async def test_call_ai_once_non_claude_ignores_cached_mc(self):
        """Non-Claude providers should not attempt to pass use_cached_microcompact."""
        from ai_client import call_ai_once
        captured = {}

        async def fake_once_openai(url, api_key, model, system_prompt,
                                   messages, temperature, max_tokens, tools):
            captured["called"] = True
            return {"type": "text", "content": "ok"}

        with patch("ai_client._require_key", return_value="fake-key"), \
             patch("ai_client._once_openai_compat", new=fake_once_openai):
            await call_ai_once(
                "sp", [{"role": "user", "content": "hi"}],
                provider="deepseek", model="deepseek-chat",
                use_cached_microcompact=True,  # passed but should be ignored for non-claude
            )

        self.assertTrue(captured.get("called"))


class TestRetryAndRateLimit(unittest.IsolatedAsyncioTestCase):

    def test_parse_retry_after_ms(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after-ms": "5000"}), 5.0)

    def test_parse_retry_after_seconds(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after": "3"}), 3.0)

    def test_parse_retry_after_ms_takes_priority(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after-ms": "2000", "retry-after": "10"}), 2.0)

    def test_parse_retry_after_no_header_returns_default(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({}), 2.0)

    def test_parse_retry_after_invalid_value_returns_default(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after": "not-a-number"}), 2.0)

    async def test_call_ai_once_retries_on_rate_limit_and_succeeds(self):
        from ai_client import call_ai_once
        call_count = 0

        async def fake_dispatch(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                from ai_client import AIRateLimitError
                raise AIRateLimitError(0.001)  # tiny wait for test speed
            return {"type": "text", "content": "ok"}

        with patch("ai_client._dispatch_once", new=fake_dispatch), \
             patch("ai_client._keys", return_value={}), \
             patch("ai_client.asyncio.sleep", new=AsyncMock()):
            result = await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                        provider="deepseek", model="deepseek-chat")

        self.assertEqual(result["content"], "ok")
        self.assertEqual(call_count, 2)

    async def test_call_ai_once_exhausted_raises_ai_error(self):
        from ai_client import call_ai_once, AIError, AIRateLimitError

        async def always_rate_limit(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            raise AIRateLimitError(0.001)

        with patch("ai_client._dispatch_once", new=always_rate_limit), \
             patch("ai_client._keys", return_value={}), \
             patch("ai_client.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(AIError):
                await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                   provider="deepseek", model="deepseek-chat")

    async def test_call_ai_once_uses_fallback_model_after_rate_limit(self):
        from ai_client import call_ai_once, AIRateLimitError
        tried_models = []

        async def fake_dispatch(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            tried_models.append(model)
            if model == "main-model":
                raise AIRateLimitError(0.001)
            return {"type": "text", "content": "fallback ok"}

        with patch("ai_client._dispatch_once", new=fake_dispatch), \
             patch("ai_client._keys", return_value={}), \
             patch("ai_client.asyncio.sleep", new=AsyncMock()):
            result = await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                        provider="deepseek", model="main-model",
                                        fallback_model="fallback-model")

        self.assertIn("fallback-model", tried_models)
        self.assertEqual(result["content"], "fallback ok")


if __name__ == "__main__":
    unittest.main()
