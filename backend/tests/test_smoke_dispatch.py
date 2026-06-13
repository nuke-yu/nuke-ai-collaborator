"""
Smoke test — the real bot-response path end to end, WITHOUT mocking interaction.

This is the regression net for the DFT-058 class of bug: the production
ExecutionContext construction sites (here: core.orchestrator.dispatch_bots ->
race_role_group) left ctx.interaction unset, and tool_loop_v1 crashed
(NameError / AttributeError) on the first side effect. Every existing test that
touched dispatch_bots mocked it away (test_event_dispatch patches dispatch_bots
itself) or injected a mock interaction (test_dynamic_context), so the real chain
was never exercised and the bug shipped while the suite stayed green.

Here we mock ONLY the AI seam (call_ai_once / call_ai_stream_messages) and the
noisy fire-and-forget side effects. dispatch_bots, ExecutionContext, the
registry, tool_loop_v1, and the real StandardInteraction all run for real
against a temp SQLite DB. The assertion: a bot reply is actually persisted —
which can only happen if the whole orchestrator -> executor -> interaction ->
DB chain works.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
import db.writer as _writer_mod
from db.schema import init_db

_TEST_DB = str(Path(__file__).parent.parent / "test_smoke_dispatch.db")

BOT_ID = 1
HUMAN_ID = 2
GROUP_ID = 1


class TestSmokeDispatch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_db = _db_mod.DB_PATH
        self._orig_writer = _writer_mod.DB_PATH
        # Both the read helper (db.connect) and the serialized writer
        # (db.writer.write_connect) must point at the temp DB — the real
        # StandardInteraction.save_message writes through the writer.
        _db_mod.DB_PATH = _TEST_DB
        _writer_mod.DB_PATH = _TEST_DB
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
            await db.execute("INSERT INTO groups (id, name) VALUES (?, 'g')", (GROUP_ID,))
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role, system_prompt,"
                " model_provider, model_name, avatar_color) "
                "VALUES (?, ?, 'Assistant', 'bot', 'assistant', 's', 'deepseek',"
                " 'deepseek-chat', '#fff')",
                (BOT_ID, GROUP_ID),
            )
            await db.execute(
                "INSERT INTO members (id, group_id, name, type) VALUES (?, ?, 'Human', 'human')",
                (HUMAN_ID, GROUP_ID),
            )
            await db.commit()
        # Load executor plugins (production does this in main.py lifespan).
        from executors import registry
        registry.discover()

    async def asyncTearDown(self):
        from db import aclose_writer
        await aclose_writer()
        _db_mod.DB_PATH = self._orig_db
        _writer_mod.DB_PATH = self._orig_writer
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_dispatch_bots_persists_a_bot_reply_with_real_interaction(self):
        bot = {
            "id": BOT_ID, "name": "Assistant", "role": "assistant",
            "avatar_color": "#fff", "type": "bot", "system_prompt": "s",
            "model_provider": "deepseek", "model_name": "deepseek-chat",
            "temperature": 0.7, "max_tokens": 4096, "executor_config": {},
            "context_cleared_at": None,
        }
        human = {"id": HUMAN_ID, "name": "Human", "type": "human", "sender_type": "human"}

        async def mock_stream(*args, **kwargs):
            yield "smoke reply"
            if isinstance(kwargs.get("usage_out"), list):
                kwargs["usage_out"].append({"input_tokens": 3, "output_tokens": 2})

        call_once = AsyncMock(return_value={
            "type": "text", "content": "smoke reply",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        })

        m = "executors.plugins.tool_loop_v1."
        with patch("core.orchestration.ai_service.call_ai_once", new=call_once), \
             patch("core.orchestration.ai_service.call_ai_stream_messages", side_effect=mock_stream), \
             patch("ai.memory.get_memory_context", new=AsyncMock(return_value="")), \
             patch(m + "list_skills_all", new=AsyncMock(return_value=[])), \
             patch(m + "load_always_skills", new=AsyncMock(return_value=[])), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch(m + "format_context_blocks", return_value=""), \
             patch(m + "filter_skills_by_context", side_effect=lambda s, _: s), \
             patch("ai.memory.add_to_chroma", new=AsyncMock()), \
             patch("ai.memory.maybe_summarize", new=AsyncMock()), \
             patch(m + "append_log", new=AsyncMock()), \
             patch(m + "archive_run", new=AsyncMock()), \
             patch("executors.compact.maybe_compact_db_history", new=AsyncMock()):
            # Drive the current chain directly: a WorkUnit run through
            # runner.run_unit (bot selection is covered separately). No interaction
            # is passed — run_unit wires the real StandardInteraction, which must
            # persist the reply (DFT-058). If that chain regresses this raises.
            from core.orchestration.base import WorkUnit
            from core.orchestration import registry as orch_registry
            from core import runner
            orch = orch_registry.get("workflow_v1")
            await runner.run_unit(GROUP_ID, WorkUnit(bot=bot, trigger_msg="hello"), orch)

        import aiosqlite
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute(
                "SELECT content FROM messages WHERE member_id=? AND group_id=?",
                (BOT_ID, GROUP_ID),
            ) as cur:
                rows = await cur.fetchall()

        self.assertTrue(
            rows,
            "no bot reply persisted — the orchestrator -> ExecutionContext -> "
            "tool_loop_v1 -> StandardInteraction -> DB chain is broken (DFT-058 class)",
        )
        self.assertIn("smoke reply", rows[-1][0])


if __name__ == "__main__":
    unittest.main()
