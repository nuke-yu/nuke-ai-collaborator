"""
tests/test_image_understanding.py — 图片理解功能单元测试

覆盖：
  1. build_image_content — 各 provider 分支
  2. _to_claude_messages — image_url 块转 Claude 格式
  3. _text_only — 多模态内容提取纯文本
  4. ExecutionContext — 新增 file_url / file_type 字段
  5. call_ai_stream — 非视觉 provider 降级为纯文本（mock 网络）
"""
import sys
import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.role_router import build_image_content
from ai.client import _to_claude_messages, _text_only
from executors.base import ExecutionContext


# ─── 1. build_image_content ──────────────────────────────────────────────────

class TestBuildImageContent(unittest.TestCase):

    def test_no_file_url_returns_text(self):
        result = build_image_content("hello", None, None, "openai")
        self.assertEqual(result, "hello")

    def test_non_image_mime_returns_text(self):
        result = build_image_content("hi", "http://x/doc.pdf", "application/pdf", "openai")
        self.assertEqual(result, "hi")

    def test_openai_returns_list_with_image_url_block(self):
        url = "https://example.com/img.png"
        result = build_image_content("describe this", url, "image/png", "openai")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {"type": "text", "text": "describe this"})
        self.assertEqual(result[1]["type"], "image_url")
        self.assertEqual(result[1]["image_url"]["url"], url)

    def test_claude_returns_list_with_image_url_block(self):
        url = "https://example.com/photo.jpg"
        result = build_image_content("what is this?", url, "image/jpeg", "claude")
        self.assertIsInstance(result, list)
        self.assertEqual(result[1]["type"], "image_url")
        self.assertEqual(result[1]["image_url"]["url"], url)

    def test_deepseek_returns_text_with_url_fallback(self):
        url = "https://example.com/img.png"
        result = build_image_content("look at this", url, "image/png", "deepseek")
        self.assertIsInstance(result, str)
        self.assertIn("look at this", result)
        self.assertIn(url, result)

    def test_ollama_returns_text_fallback(self):
        url = "https://example.com/img.gif"
        result = build_image_content("check image", url, "image/gif", "ollama")
        self.assertIsInstance(result, str)
        self.assertIn(url, result)

    def test_image_webp_mime_recognized(self):
        result = build_image_content("x", "http://a/b.webp", "image/webp", "openai")
        self.assertIsInstance(result, list)

    def test_empty_text_still_produces_blocks(self):
        result = build_image_content("", "http://a/img.png", "image/png", "openai")
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["text"], "")


# ─── 2. _to_claude_messages — image_url → Claude image format ────────────────

class TestToClaudeMessagesImageConversion(unittest.TestCase):

    def test_image_url_block_converted_to_claude_image(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "what is in the photo?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}},
            ]
        }]
        result = _to_claude_messages(messages)
        self.assertEqual(len(result), 1)
        content = result[0]["content"]
        self.assertIsInstance(content, list)
        text_block = next(b for b in content if b.get("type") == "text")
        img_block  = next(b for b in content if b.get("type") == "image")
        self.assertEqual(text_block["text"], "what is in the photo?")
        self.assertEqual(img_block["source"]["type"], "url")
        self.assertEqual(img_block["source"]["url"], "https://example.com/cat.jpg")

    def test_plain_text_message_unchanged(self):
        messages = [{"role": "user", "content": "hello"}]
        result = _to_claude_messages(messages)
        self.assertEqual(result[0]["content"], "hello")

    def test_list_without_image_url_unchanged(self):
        messages = [{
            "role": "user",
            "content": [{"type": "text", "text": "just text"}]
        }]
        result = _to_claude_messages(messages)
        self.assertEqual(result[0]["content"], [{"type": "text", "text": "just text"}])

    def test_mixed_blocks_preserves_text_converts_image(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "image_url", "image_url": {"url": "http://x/a.png"}},
                {"type": "text", "text": "second"},
            ]
        }]
        result = _to_claude_messages(messages)
        content = result[0]["content"]
        types = [b["type"] for b in content]
        self.assertEqual(types, ["text", "image", "text"])


# ─── 3. _text_only ───────────────────────────────────────────────────────────

class TestTextOnly(unittest.TestCase):

    def test_string_returned_as_is(self):
        self.assertEqual(_text_only("hello world"), "hello world")

    def test_list_extracts_text_blocks(self):
        content = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "http://x/img.png"}},
        ]
        result = _text_only(content)
        self.assertEqual(result, "describe this")

    def test_multiple_text_blocks_joined(self):
        content = [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        result = _text_only(content)
        self.assertIn("first", result)
        self.assertIn("second", result)

    def test_empty_list_returns_empty_string(self):
        result = _text_only([])
        self.assertEqual(result, "")

    def test_no_text_blocks_returns_empty(self):
        content = [{"type": "image_url", "image_url": {"url": "http://x/img.png"}}]
        result = _text_only(content)
        self.assertEqual(result, "")


# ─── 4. ExecutionContext new fields ──────────────────────────────────────────

class TestExecutionContextImageFields(unittest.TestCase):

    def _make_ctx(self, **kwargs):
        return ExecutionContext(
            bot={}, group_id=1, user_message="hi",
            sender={}, history=[], all_bots=[], all_members=[],
            broadcaster=None,
            **kwargs,
        )

    def test_defaults_are_none(self):
        ctx = self._make_ctx()
        self.assertIsNone(ctx.file_url)
        self.assertIsNone(ctx.file_type)

    def test_can_set_file_url_and_type(self):
        ctx = self._make_ctx(file_url="https://x.com/img.jpg", file_type="image/jpeg")
        self.assertEqual(ctx.file_url, "https://x.com/img.jpg")
        self.assertEqual(ctx.file_type, "image/jpeg")


# ─── 5. call_ai_stream — deepseek/ollama strip image to text ────────────────

class TestCallAiStreamImageFallback(unittest.IsolatedAsyncioTestCase):

    async def _collect_stream(self, gen):
        chunks = []
        async for c in gen:
            chunks.append(c)
        return "".join(chunks)

    async def test_deepseek_flattens_multimodal_to_text(self):
        multimodal = [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "http://x/img.png"}},
        ]
        captured = {}

        async def fake_stream(url, api_key, model, messages, temperature, max_tokens, usage_out=None):
            # Record the user message content that was passed
            captured["user_content"] = messages[-1]["content"]
            yield "ok"

        with patch("ai.client._stream_openai_compat", side_effect=fake_stream), \
             patch("ai.client._keys", return_value={"deepseek": "fake-key", "openai": "", "anthropic": "", "ollama_url": ""}):
            from ai.client import call_ai_stream
            await self._collect_stream(call_ai_stream(
                "sys", [], multimodal, provider="deepseek", model="deepseek-chat",
            ))

        self.assertIsInstance(captured["user_content"], str)
        self.assertIn("describe", captured["user_content"])
        self.assertNotIn("image_url", str(captured["user_content"]))

    async def test_openai_passes_list_content_unchanged(self):
        multimodal = [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "http://x/img.png"}},
        ]
        captured = {}

        async def fake_stream(url, api_key, model, messages, temperature, max_tokens, usage_out=None):
            captured["user_content"] = messages[-1]["content"]
            yield "ok"

        with patch("ai.client._stream_openai_compat", side_effect=fake_stream), \
             patch("ai.client._keys", return_value={"deepseek": "", "openai": "fake-key", "anthropic": "", "ollama_url": ""}):
            from ai.client import call_ai_stream
            await self._collect_stream(call_ai_stream(
                "sys", [], multimodal, provider="openai", model="gpt-4o",
            ))

        self.assertIsInstance(captured["user_content"], list)
        types = [b["type"] for b in captured["user_content"]]
        self.assertIn("image_url", types)

    async def test_claude_converts_image_url_to_claude_format(self):
        multimodal = [
            {"type": "text", "text": "analyze"},
            {"type": "image_url", "image_url": {"url": "http://x/photo.png"}},
        ]
        captured = {}

        async def fake_stream(model, system_prompt, messages, api_key, temperature, max_tokens, usage_out=None):
            captured["messages"] = messages
            yield "result"

        with patch("ai.client._stream_claude", side_effect=fake_stream), \
             patch("ai.client._keys", return_value={"deepseek": "", "openai": "", "anthropic": "fake-key", "ollama_url": ""}):
            from ai.client import call_ai_stream
            await self._collect_stream(call_ai_stream(
                "sys", [], multimodal, provider="claude", model="claude-3-5-sonnet-latest",
            ))

        user_msg = captured["messages"][-1]
        self.assertEqual(user_msg["role"], "user")
        img_block = next(b for b in user_msg["content"] if b.get("type") == "image")
        self.assertEqual(img_block["source"]["url"], "http://x/photo.png")


if __name__ == "__main__":
    unittest.main()
