"""Tests for user abort: CancelledError → stream_aborted broadcast in tool_loop_v1."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from executors.base import ExecutionContext, InteractionAdapter


class _MockInteraction(InteractionAdapter):
    def __init__(self):
        self.events = []

    async def broadcast(self, group_id, message):
        self.events.append(message)

    async def save_message(self, group_id, member_id, content, **kwargs):
        return 1

    async def append_session_event(self, session_id, event_type, payload):
        pass

    async def save_session_snapshot(self, session_id, messages):
        pass

    async def update_session_tokens(self, session_id, **usage):
        pass
    
    async def create_session(self, **kwargs):
        pass
        
    async def update_session_status(self, session_id, status):
        pass

    def event_types(self):
        return [e.get("type") for e in self.events]


class TestAbortSignal(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_error_broadcasts_stream_aborted(self):
        """When AI call raises CancelledError, tool_loop emits stream_aborted."""
        mock_inter = _MockInteraction()
        bot = {
            "id": 1,
            "name": "test-bot",
            "type": "bot",
            "model_name": "deepseek-chat",
            "system_prompt": "",
            "executor_id": "tool_loop_v1",
            "executor_config": {},
            "role": "dev",
            "avatar_color": "#000",
        }
        ctx = ExecutionContext(
            bot=bot,
            group_id=1,
            user_message="hello",
            sender={"name": "user"},
            history=[],
            all_bots=[bot],
            all_members=[bot],
            broadcaster=MagicMock(),
            interaction=mock_inter,
        )

        _mod = "executors.plugins.tool_loop_v1"
        
        async def mock_stream(*args, **kwargs):
            raise asyncio.CancelledError
            yield

        with patch("permissions.load_rules", new=AsyncMock(return_value=[])), \
             patch(f"{_mod}.get_memory_context", new=AsyncMock(return_value="")), \
             patch(f"{_mod}.load_context_files", new=AsyncMock(return_value=[])), \
             patch(f"{_mod}.format_context_blocks", return_value=""), \
             patch(f"{_mod}.list_skills_all", new=AsyncMock(return_value=[])), \
             patch(f"{_mod}.load_always_skills", new=AsyncMock(return_value=[])), \
             patch(f"{_mod}.filter_skills_by_context", side_effect=lambda s, _: s), \
             patch(f"{_mod}.build_context_message", return_value=([], "hello")), \
             patch(f"{_mod}.append_log", new=AsyncMock()), \
             patch(f"{_mod}.archive_run", new=AsyncMock()), \
             patch("core.orchestration.ai_service.call_ai_stream_messages", side_effect=mock_stream):
            
            from executors.plugins.tool_loop_v1 import ToolLoopV1
            executor = ToolLoopV1()
            
            # The executor.run should catch CancelledError and broadcast stream_aborted
            try:
                await executor.run(ctx)
            except asyncio.CancelledError:
                pass

        self.assertIn("stream_aborted", mock_inter.event_types(),
                      f"expected stream_aborted; got {mock_inter.event_types()}")

if __name__ == "__main__":
    unittest.main()
