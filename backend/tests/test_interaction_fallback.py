"""
DFT-058 regression: production callers (orchestrator / runner / recovery /
spawn_agent) construct ExecutionContext WITHOUT an interaction adapter, leaving
ctx.interaction is None. tool_loop_v1 must fall back to the real
StandardInteraction instead of crashing with AttributeError on the first
ctx.interaction.* call.

Before the fix, every real bot run died (NameError in orchestrator / AttributeError
elsewhere) while the suite stayed green because every test injected a mock. This
test deliberately leaves interaction=None to lock the fallback in place.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from executors.base import ExecutionContext, InteractionAdapter
from executors.plugins.tool_loop_v1 import ToolLoopV1


class _MockInteraction(InteractionAdapter):
    def __init__(self):
        self.broadcasts = []
        self.messages = []
        self.events = []

    async def create_session(self, **kwargs):
        self.events.append(("create_session", kwargs))

    async def update_session_status(self, session_id, status):
        self.events.append(("update_status", session_id, status))

    async def broadcast(self, group_id, payload):
        self.broadcasts.append((group_id, payload))

    async def save_message(self, group_id, member_id, content, **kwargs):
        self.messages.append((group_id, member_id, content, kwargs))
        return 123

    async def append_session_event(self, session_id, event_type, payload):
        self.events.append((session_id, event_type, payload))

    async def save_session_snapshot(self, session_id, messages):
        pass

    async def update_session_tokens(self, session_id, **usage):
        pass


def _bot():
    return {
        "id": 1, "name": "Bot", "role": "dev", "avatar_color": "#fff",
        "type": "bot", "system_prompt": "s", "model_provider": "deepseek",
        "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096,
        "executor_config": {},
    }


class TestInteractionFallback(unittest.IsolatedAsyncioTestCase):
    async def test_none_interaction_falls_back_to_standard(self):
        bot = _bot()
        ctx = ExecutionContext(
            bot=bot, group_id=1, user_message="task",
            sender={"id": 2, "name": "Human", "type": "human"},
            history=[], all_bots=[bot], all_members=[bot],
            interaction=None,   # <-- the production condition that used to crash
        )

        fake = _MockInteraction()

        async def mock_stream(*args, **kwargs):
            yield "hello"
            if isinstance(kwargs.get("usage_out"), list):
                kwargs["usage_out"].append({"input_tokens": 10, "output_tokens": 5})

        m = "executors.plugins.tool_loop_v1."
        with patch("core.orchestration.interaction.StandardInteraction", return_value=fake), \
             patch("core.orchestration.ai_service.call_ai_stream_messages", side_effect=mock_stream), \
             patch("core.orchestration.ai_service.call_ai_once",
                   new=AsyncMock(return_value={"type": "text", "content": "hello", "usage": {}})), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch("ai.memory.get_memory_context", new=AsyncMock(return_value="")), \
             patch(m + "list_skills_all", return_value=[]), \
             patch(m + "get_db", new=MagicMock()), \
             patch("ai.memory.add_to_chroma", new=AsyncMock()), \
             patch("ai.memory.maybe_summarize", new=AsyncMock()), \
             patch(m + "append_log", new=AsyncMock()), \
             patch(m + "archive_run", new=AsyncMock()), \
             patch("executors.compact.apply_tool_result_microcompact", side_effect=lambda x: x):
            executor = ToolLoopV1()
            # Must not raise AttributeError: 'NoneType' has no attribute 'broadcast'
            await executor.run(ctx)

        # Fallback fired: ctx.interaction was populated and side effects flowed.
        self.assertIs(ctx.interaction, fake)
        self.assertGreater(len(fake.broadcasts), 0)
        self.assertEqual(fake.broadcasts[0][1]["type"], "stream_start")
        self.assertEqual(len(fake.messages), 1)


if __name__ == "__main__":
    unittest.main()
