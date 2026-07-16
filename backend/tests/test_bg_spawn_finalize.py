"""
DFT-063 regression: tool_loop_v1's finalize side effects (add_to_chroma,
maybe_summarize, maybe_compact_db_history, append_log, archive_run) were fired
with bare asyncio.create_task — no strong reference (GC could kill them
mid-flight) and exceptions silently swallowed, despite DFT-025 claiming all
fire-and-forget work goes through bg.spawn.

This test spies core.bg.spawn and asserts the finalize side effects are
scheduled through it (held + exception-logged), not via a raw create_task.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from executors.base import ExecutionContext, InteractionAdapter
from executors.plugins.tool_loop_v1 import ToolLoopV1


class _MockInteraction(InteractionAdapter):
    def __init__(self):
        self.events = []

    async def create_session(self, **kwargs): pass
    async def update_session_status(self, session_id, status):
        self.events.append(("status", status))
    async def broadcast(self, group_id, payload): pass
    async def save_message(self, group_id, member_id, content, **kwargs): return 123
    async def append_session_event(self, session_id, event_type, payload): pass
    async def save_session_snapshot(self, session_id, messages):
        self.events.append(("snapshot", session_id))
    async def update_session_tokens(self, session_id, **usage): pass


def _bot():
    return {
        "id": 1, "name": "Bot", "role": "dev", "avatar_color": "#fff",
        "type": "bot", "system_prompt": "s", "model_provider": "deepseek",
        "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096,
        "executor_config": {},
    }


class TestBgSpawnFinalize(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_side_effects_go_through_bg_spawn(self):
        interaction = _MockInteraction()
        ctx = ExecutionContext(
            bot=_bot(), group_id=1, user_message="task",
            sender={"id": 2, "name": "Human", "type": "human"},
            history=[], all_bots=[_bot()], all_members=[_bot()],
            interaction=interaction,
        )

        spawned = []

        def fake_spawn(coro):
            interaction.events.append(("spawn", None))
            spawned.append(getattr(coro, "__qualname__", "") or getattr(coro, "__name__", ""))
            coro.close()  # we only assert it was scheduled via bg.spawn
            return MagicMock()

        async def mock_stream(*args, **kwargs):
            yield "done"
            if isinstance(kwargs.get("usage_out"), list):
                kwargs["usage_out"].append({"input_tokens": 1, "output_tokens": 1})

        m = "executors.plugins.tool_loop_v1."
        with patch("core.bg.spawn", new=fake_spawn), \
             patch("core.orchestration.ai_service.call_ai_stream_messages", side_effect=mock_stream), \
             patch("core.orchestration.ai_service.call_ai_once",
                   new=AsyncMock(return_value={"type": "text", "content": "done", "usage": {}})), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch("ai.memory.get_memory_context", new=AsyncMock(return_value="")), \
             patch(m + "list_skills_all", new=AsyncMock(return_value=[])), \
             patch(m + "load_always_skills", new=AsyncMock(return_value=[])), \
             patch(m + "get_db", new=MagicMock()), \
             patch("ai.memory.add_to_chroma", new=AsyncMock()), \
             patch("ai.memory.maybe_summarize", new=AsyncMock()), \
             patch(m + "append_log", new=AsyncMock()), \
             patch(m + "archive_run", new=AsyncMock()), \
             patch("executors.compact.maybe_compact_db_history", new=AsyncMock()), \
             patch("executors.compact.apply_tool_result_microcompact", side_effect=lambda x: x):
            await ToolLoopV1().run(ctx)

        # 记忆写路径现已收敛为单个 memory.observe（内部触发 ingest+summarize+reflect），
        # 与 compaction publish、append_log 一并经 bg.spawn 调度，而非裸 asyncio.create_task。
        self.assertGreaterEqual(
            len(spawned), 3,
            f"expected finalize side effects via bg.spawn, got {spawned} "
            "(a regression to bare asyncio.create_task)",
        )
        self.assertTrue(
            any("observe" in n for n in spawned),
            f"记忆写路径 (memory.observe) 必须经 bg.spawn 调度，实际: {spawned}",
        )
        completed_index = interaction.events.index(("status", "completed"))
        first_spawn_index = interaction.events.index(("spawn", None))
        self.assertLess(completed_index, first_spawn_index)


if __name__ == "__main__":
    unittest.main()
