import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from executors.base import ExecutionContext, InteractionAdapter
from executors.plugins.tool_loop_v1 import ToolLoopV1

class MockInteraction(InteractionAdapter):
    def __init__(self):
        self.broadcasts = []
        self.messages = []
        self.events = []
        self.snapshots = []
        self.tokens = []

    async def create_session(self, **kwargs):
        self.events.append(("create_session", kwargs))

    async def update_session_status(self, session_id, status):
        self.events.append(("update_status", session_id, status))

    async def broadcast(self, group_id, payload):
        self.broadcasts.append((group_id, payload))

    async def save_message(self, group_id, member_id, content, **kwargs):
        self.messages.append((group_id, member_id, content, kwargs))
        return 123 # msg_id

    async def append_session_event(self, session_id, event_type, payload):
        self.events.append((session_id, event_type, payload))

    async def save_session_snapshot(self, session_id, messages):
        self.snapshots.append((session_id, messages))

    async def update_session_tokens(self, session_id, **usage):
        self.tokens.append((session_id, usage))

class TestDecoupledExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_tool_loop_uses_injected_interaction(self):
        # 1. Setup mock interaction (The "Pure" test environment)
        mock_inter = MockInteraction()
        
        bot = {
            "id": 1, "name": "Bot", "role": "dev", "avatar_color": "#fff",
            "type": "bot", "system_prompt": "s", "model_provider": "deepseek",
            "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096,
            "executor_config": {},
        }
        ctx = ExecutionContext(
            bot=bot, group_id=1, user_message="task",
            sender={"id": 2, "name": "Human", "type": "human"},
            history=[], all_bots=[bot], all_members=[bot], # Should not be used anymore
            interaction=mock_inter,
        )

        # 2. Mock AI and other external calls
        from unittest.mock import patch
        m = "executors.plugins.tool_loop_v1."
        
        async def mock_stream(*args, **kwargs):
            yield "hello"
            if "usage_out" in kwargs and isinstance(kwargs["usage_out"], list):
                kwargs["usage_out"].append({"input_tokens": 10, "output_tokens": 5})

        # Path to mock: core.orchestration.ai_service uses call_ai_once and call_ai_stream_messages
        with patch("core.orchestration.ai_service.call_ai_once", new=AsyncMock(return_value={"type": "text", "content": "hello", "usage": {}})), \
             patch("core.orchestration.ai_service.call_ai_stream_messages", side_effect=mock_stream), \
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
            await executor.run(ctx)

        # 3. Verify side-effects went through our Mock instead of real DB/Bus
        self.assertGreater(len(mock_inter.broadcasts), 0)
        self.assertEqual(mock_inter.broadcasts[0][1]["type"], "stream_start")
        
        self.assertEqual(len(mock_inter.messages), 1)
        self.assertEqual(mock_inter.messages[0][2], "hello") # content
        
        self.assertGreater(len(mock_inter.events), 1)
        # Event 0 is create_session (a tuple with "create_session")
        self.assertEqual(mock_inter.events[0][0], "create_session")
        # Event 1 is append_session_event for "session_start"
        self.assertEqual(mock_inter.events[1][1], "session_start")

if __name__ == "__main__":
    unittest.main()
