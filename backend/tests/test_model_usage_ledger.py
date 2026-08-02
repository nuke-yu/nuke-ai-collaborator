"""Request lifecycle wiring from AIService into the durable usage ledger events."""

import types
import unittest
from unittest.mock import AsyncMock, patch

from ai.client import AIContextOverflowError, AIError
from core.orchestration.ai_service import AIService


class _Interaction:
    def __init__(self):
        self.events = []
        self.update_session_tokens = AsyncMock()
        self.broadcast = AsyncMock()

    async def append_session_event(self, session_id, event_type, payload):
        self.events.append((session_id, event_type, payload))


def _service():
    interaction = _Interaction()
    ctx = types.SimpleNamespace(
        interaction=interaction,
        group_id=7,
        active_ticket_id="T-7",
        bot={"model_provider": "deepseek", "model_name": "deepseek-chat"},
    )
    return AIService(ctx, "session-7", "temp-7"), interaction


class TestAIServiceUsageLedger(unittest.IsolatedAsyncioTestCase):
    async def test_nonstream_request_records_exact_model_and_usage(self):
        service, interaction = _service()
        result = {
            "type": "tool_calls", "calls": [],
            "usage": {"input_tokens": 12, "output_tokens": 3, "cache_read_tokens": 4},
        }
        with patch(
            "core.orchestration.ai_service.call_ai_once",
            new=AsyncMock(return_value=result),
        ):
            await service.call("sp", [], "gpt-4o", "openai", operation="skill_fork")

        self.assertEqual(
            [event[1] for event in interaction.events],
            ["model_request_started", "model_request_completed"],
        )
        started, completed = interaction.events[0][2], interaction.events[1][2]
        self.assertEqual(started["provider"], "openai")
        self.assertEqual(started["model"], "gpt-4o")
        self.assertEqual(started["operation"], "skill_fork")
        self.assertEqual(completed["request_id"], started["request_id"])
        self.assertEqual(completed["response_type"], "tool_calls")
        self.assertEqual(completed["input_tokens"], 12)
        self.assertEqual(completed["ticket_id"], "T-7")
        interaction.update_session_tokens.assert_not_awaited()

    async def test_failed_request_closes_without_usage(self):
        service, interaction = _service()
        with patch(
            "core.orchestration.ai_service.call_ai_once",
            new=AsyncMock(side_effect=AIError("provider unavailable")),
        ):
            with self.assertRaises(AIError):
                await service.call("sp", [], "deepseek-chat", "deepseek")
        failed = interaction.events[-1]
        self.assertEqual(failed[1], "model_request_failed")
        self.assertEqual(failed[2]["error_type"], "AIError")
        self.assertEqual(failed[2]["input_tokens"], 0)

    async def test_overflow_retry_is_a_distinct_linked_request(self):
        service, interaction = _service()
        result = {"type": "text", "content": "ok", "usage": {"input_tokens": 5}}
        with patch(
            "core.orchestration.ai_service.call_ai_once",
            new=AsyncMock(side_effect=[AIContextOverflowError("too long"), result]),
        ), patch(
            "core.orchestration.ai_service.compact.compact_conversation",
            new=AsyncMock(return_value=[{"role": "user", "content": "summary"}]),
        ):
            await service.call("sp", [{"role": "user", "content": "long"}],
                               "deepseek-chat", "deepseek")

        self.assertEqual(
            [event[1] for event in interaction.events],
            [
                "model_request_started", "model_request_failed",
                "model_request_started", "model_request_completed",
            ],
        )
        first = interaction.events[0][2]
        retry = interaction.events[2][2]
        self.assertEqual(retry["request_ordinal"], 2)
        self.assertEqual(retry["retry_of"], first["request_id"])

    async def test_stream_request_records_terminal_usage(self):
        service, interaction = _service()

        async def fake_stream(*args, **kwargs):
            kwargs["usage_out"].append({"input_tokens": 9, "output_tokens": 2})
            yield "hello"

        with patch(
            "core.orchestration.ai_service.call_ai_stream_messages",
            new=fake_stream,
        ):
            chunks = [chunk async for chunk in service.stream(
                "sp", [], "claude-sonnet", "claude"
            )]
        self.assertEqual(chunks, ["hello"])
        self.assertEqual(interaction.events[-1][1], "model_request_completed")
        self.assertEqual(interaction.events[-1][2]["output_tokens"], 2)
        self.assertTrue(interaction.events[0][2]["streaming"])


if __name__ == "__main__":
    unittest.main()
