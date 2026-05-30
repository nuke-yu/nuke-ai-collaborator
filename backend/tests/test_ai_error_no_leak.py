"""
tests/test_ai_error_no_leak.py — DFT-051 AIError 不再回显原始异常

ai/client.py 多处 `raise AIError(f"AI 调用失败：{str(e)}")` 把底层异常（可能含
请求 URL / Authorization header / 内网地址）原样塞进 AIError；消费方
(simple_v1 / orchestrator 等) 又 `str(e)` 广播成 stream_error 发给聊天 → 敏感
信息泄漏到用户侧。现在用户侧只给通用文案，详细 str(e) 仅经 logger 入日志。
"""
import os
import sys
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai.client as client

SECRET = "https://internal.example/v1?Authorization=Bearer_SUPERSECRET_TOKEN_123"

_FAKE_KEYS = {
    "deepseek": "k",
    "openai": "k",
    "anthropic": "k",
    "ollama_url": "http://localhost:11434",
}


class TestAIErrorNoLeak(unittest.IsolatedAsyncioTestCase):

    async def test_call_ai_message_is_generic_and_logs_detail(self):
        fake_client = MagicMock()
        fake_client.post = AsyncMock(side_effect=RuntimeError(SECRET))

        @asynccontextmanager
        async def fake_shared():
            yield fake_client

        with patch.object(client, "_keys", return_value=_FAKE_KEYS), \
             patch.object(client, "_shared_client", fake_shared):
            with self.assertLogs("ai.client", level="ERROR") as logs:
                with self.assertRaises(client.AIError) as cm:
                    await client.call_ai("sys", [], "hi")

        # user-facing message must not contain the secret
        self.assertNotIn("SUPERSECRET", str(cm.exception))
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertIn("AI 调用失败", str(cm.exception))
        # but the detail must reach the logs (via exc_info chain)
        self.assertTrue(logs.records)
        logged = "".join(str(r.exc_info[1]) for r in logs.records if r.exc_info)
        self.assertIn("SUPERSECRET", logged)

    async def test_call_ai_stream_message_is_generic(self):
        async def boom_stream(*a, **k):
            raise RuntimeError(SECRET)
            yield  # pragma: no cover — makes this an async generator

        with patch.object(client, "_keys", return_value=_FAKE_KEYS), \
             patch.object(client, "_stream_openai_compat", boom_stream):
            with self.assertRaises(client.AIError) as cm:
                async for _ in client.call_ai_stream("sys", [], "hi", provider="deepseek"):
                    pass

        self.assertNotIn("SUPERSECRET", str(cm.exception))
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertIn("AI 调用失败", str(cm.exception))

    async def test_call_ai_stream_messages_is_generic(self):
        async def boom_stream(*a, **k):
            raise RuntimeError(SECRET)
            yield  # pragma: no cover

        with patch.object(client, "_keys", return_value=_FAKE_KEYS), \
             patch.object(client, "_stream_openai_compat", boom_stream):
            with self.assertRaises(client.AIError) as cm:
                async for _ in client.call_ai_stream_messages(
                    "sys", [{"role": "user", "content": "hi"}], provider="deepseek"
                ):
                    pass

        self.assertNotIn("SUPERSECRET", str(cm.exception))
        self.assertIn("AI 调用失败", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
