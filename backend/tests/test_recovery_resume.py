"""Tests for crash-recovery resume (DFT-018 / DFT-019)."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
import db.writer as _writer_mod
from db.schema import init_db
import sessions
from executors.base import ExecutionContext, ExecutionResult, InteractionAdapter
from executors.plugins.tool_loop_v1 import ToolLoopV1

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_recovery_resume.db")

class MockInteraction(InteractionAdapter):
    async def broadcast(self, group_id, payload): pass
    async def save_message(self, group_id, member_id, content, **kwargs): return 123
    async def append_session_event(self, session_id, event_type, payload): pass
    async def save_session_snapshot(self, session_id, messages): pass
    async def update_session_tokens(self, session_id, **usage): pass
    async def create_session(self, **kwargs): pass
    async def update_session_status(self, session_id, status):
        async with _db_mod.connect() as db:
            await db.execute("UPDATE agent_sessions SET status = ? WHERE id = ?", (status, session_id))
            await db.commit()

class TestResumeRunClosesSession(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        # sessions.store writes via the serialized writer (db.write_connect),
        # which resolves from db.writer.DB_PATH — patch it too.
        self._orig_writer = _writer_mod.DB_PATH
        _db_mod.DB_PATH = _TEST_DB
        _writer_mod.DB_PATH = _TEST_DB
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        async with _db_mod.connect() as db:
            from db.migrations import run_migrations
            await run_migrations(db)
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'G')")
            await db.execute("INSERT INTO members (id, group_id, name, type) VALUES (7, 1, 'B', 'bot')")
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        _writer_mod.DB_PATH = self._orig_writer
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def _count_sessions(self):
        async with _db_mod.connect() as db:
            async with db.execute("SELECT COUNT(*) FROM agent_sessions") as cur:
                row = await cur.fetchone()
                return row[0]

    async def test_resume_reuses_session_and_completes(self):
        sid = "recovery-1"
        # Seed an orphan session in 'running' state
        await sessions.create_session(
            session_id=sid, bot_id=7, group_id=1,
            config={"p":"v"}, user_message="task"
        )
        
        resume_messages = [
            {"role": "system", "content": "sp"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "call tool", "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc1", "name": "write_file", "content": "done"},
        ]

        captured_stream_msgs = {}
        async def fake_stream(sp, msgs, *a, **kw):
            captured_stream_msgs["msgs"] = list(msgs)
            yield "result"

        async def fake_call_ai_once(sp, msgs, *a, **kw):
            captured_stream_msgs["msgs"] = list(msgs)
            return {"type": "text", "content": "final answer", "usage": {}}

        bot = {
            "id": 7, "name": "Worker", "role": "dev", "avatar_color": "#fff",
            "type": "bot", "system_prompt": "orig", "model_provider": "deepseek",
            "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096,
            "executor_config": {},
        }
        ctx = ExecutionContext(
            bot=bot, group_id=1, user_message="original task",
            sender={"id": 0, "name": "系统恢复", "type": "system"},
            history=[], all_bots=[bot], all_members=[bot],
            interaction=MockInteraction(),
            resume_session_id=sid,
            resume_messages=resume_messages,
        )

        m = "executors.plugins.tool_loop_v1."
        
        with patch("core.orchestration.ai_service.call_ai_stream_messages", side_effect=fake_stream), \
             patch("core.orchestration.ai_service.call_ai_once", side_effect=fake_call_ai_once), \
             patch("ai.memory.get_memory_context", new=AsyncMock(return_value="")), \
             patch(m + "list_skills_all", new=AsyncMock(return_value=[])), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch(m + "format_context_blocks", return_value=""), \
             patch(m + "load_always_skills", new=AsyncMock(return_value=[])), \
             patch("executors.plugins.tool_loop_v1.tool_executor.get_schemas",
                   return_value=[{"function": {"name": "write_file"}}]), \
             patch("ai.memory.add_to_chroma", new=AsyncMock()), \
             patch("ai.memory.maybe_summarize", new=AsyncMock()), \
             patch(m + "append_log", new=AsyncMock()), \
             patch(m + "archive_run", new=AsyncMock()), \
             patch("executors.compact.maybe_compact_db_history", new=AsyncMock()), \
             patch("permissions.load_rules", new=AsyncMock(return_value=[])):
            result = await ToolLoopV1().run(ctx)

        self.assertIsInstance(result, ExecutionResult)
        # DFT-019: session was reused (no new row) and closed as completed.
        self.assertEqual(await self._count_sessions(), 1)
        row = await sessions.get_session(sid)
        self.assertEqual(row["status"], "completed")
        # DFT-018: the resumed conversation (with the already-completed tool
        # result) was fed to the model — not a fresh task rebuilt from history.
        msgs = captured_stream_msgs["msgs"]
        self.assertNotEqual(msgs[0]["role"], "system")  # leading system stripped
        self.assertTrue(any(mm.get("role") == "tool" and mm.get("tool_call_id") == "tc1"
                            for mm in msgs))


if __name__ == "__main__":
    unittest.main()
