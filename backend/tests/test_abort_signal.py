"""Tests for user abort: CancelledError → stream_aborted broadcast in tool_loop_v1."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from executors.base import ExecutionContext


class _MockBroadcaster:
    def __init__(self):
        self.events = []

    async def broadcast(self, group_id, message):
        self.events.append(message)

    def event_types(self):
        return [e.get("type") for e in self.events]


class TestAbortSignal(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_error_broadcasts_stream_aborted(self):
        """When call_ai_once raises CancelledError, tool_loop emits stream_aborted."""
        broadcaster = _MockBroadcaster()
        bot = {
            "id": 1,
            "name": "test-bot",
            "model": "gpt-4o-mini",
            "model_name": "deepseek-chat",
            "system_prompt": "",
            "executor": "tool_loop_v1",
            "executor_config": {},
            "tools": [],
            "role": "",
            "avatar_color": "#000000",
        }
        ctx = ExecutionContext(
            bot=bot,
            group_id=1,
            user_message="hello",
            sender={"name": "user"},
            history=[],
            all_bots=[],
            all_members=[],
            broadcaster=broadcaster,
        )

        # When no tool_schemas, tool_loop uses call_ai_stream_messages (async generator).
        # Raise CancelledError inside the generator to simulate mid-stream abort.
        async def _cancelled_stream(*args, **kwargs):
            raise asyncio.CancelledError
            yield  # makes this an async generator

        _mod = "executors.plugins.tool_loop_v1"
        patches = [
            patch("permissions.load_rules", new=AsyncMock(return_value=[])),
            patch(f"{_mod}.get_memory_context", new=AsyncMock(return_value="")),
            patch(f"{_mod}.load_context_files", new=AsyncMock(return_value=[])),
            patch(f"{_mod}.format_context_blocks", return_value=""),
            patch(f"{_mod}.list_skills_all", return_value=[]),
            patch(f"{_mod}.load_always_skills", return_value=[]),
            patch(f"{_mod}.filter_skills_by_context", side_effect=lambda s, _: s),
            patch(f"{_mod}.build_context_message", return_value=([], "hello")),
            patch(f"{_mod}.call_ai_stream_messages", new=_cancelled_stream),
            patch(f"{_mod}.append_log", new=AsyncMock()),
        ]

        for p in patches:
            p.start()
        try:
            from executors.plugins import tool_loop_v1
            executor = tool_loop_v1.ToolLoopV1()
            task = asyncio.create_task(executor.run(ctx))
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            for p in patches:
                p.stop()

        assert "stream_aborted" in broadcaster.event_types(), (
            f"expected stream_aborted; got {broadcaster.event_types()}"
        )
