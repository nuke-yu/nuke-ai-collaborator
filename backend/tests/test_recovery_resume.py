"""Tests for crash-recovery resume (DFT-018 / DFT-019).

DFT-018: recovery continues a crashed session from its reconstructed WAL
messages via a dedicated executor entry — it must NOT re-run the task from
group history (which would re-execute completed side-effectful tools).

DFT-019: a resumed session reuses its session_id, so the normal
completed/failed writeback closes the orphan instead of leaving it stuck in
'recovering'.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db


_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_recovery_resume.db")


# ---------------------------------------------------------------------------
# ExecutionContext resume fields default to None
# ---------------------------------------------------------------------------

class TestExecutionContextResumeDefaults(unittest.TestCase):

    def test_resume_fields_default_none(self):
        from executors.base import ExecutionContext
        ctx = ExecutionContext(
            bot={}, group_id=1, user_message="x", sender={}, history=[],
            all_bots=[], all_members=[], broadcaster=None,
        )
        self.assertIsNone(ctx.resume_session_id)
        self.assertIsNone(ctx.resume_messages)


# ---------------------------------------------------------------------------
# _dispatch_recovery uses the dedicated executor entry (DFT-018)
# ---------------------------------------------------------------------------

class TestDispatchRecoveryEntry(unittest.IsolatedAsyncioTestCase):

    async def _run_dispatch(self, payload, run_side_effect=None):
        """Call _dispatch_recovery with all external deps mocked; return captured ctx."""
        from sessions import recovery

        captured = {}

        async def fake_run(ctx):
            captured["ctx"] = ctx
            if run_side_effect:
                run_side_effect()
            from executors.base import ExecutionResult
            return ExecutionResult(full_text="ok", msg_id=1)

        fake_executor = MagicMock()
        fake_executor.run = AsyncMock(side_effect=fake_run)

        fake_db = MagicMock()
        fake_db.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db.__aexit__ = AsyncMock(return_value=False)

        with patch("db.get_db", return_value=fake_db), \
             patch("db.get_member", new=AsyncMock(return_value={"id": 99, "name": "Bot", "type": "bot"})), \
             patch("db.get_members", new=AsyncMock(return_value=[{"id": 99, "name": "Bot", "type": "bot"}])), \
             patch("db.get_messages", new=AsyncMock(return_value=[])), \
             patch("executors.registry.get", return_value=fake_executor), \
             patch("ws_manager.manager.broadcast", new=AsyncMock()), \
             patch("sessions.recovery.update_session_status", new=AsyncMock()) as mock_status:
            await recovery._dispatch_recovery(payload)
        return captured, fake_executor, mock_status

    async def test_passes_resume_params_to_executor(self):
        payload = {
            "session_id": "sX", "bot_id": 99, "group_id": 1,
            "config": {}, "user_message": "do the thing",
            "messages": [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "do the thing"},
                {"role": "assistant", "content": "partial"},
            ],
            "executor_id": "tool_loop_v1",
        }
        captured, fake_executor, _ = await self._run_dispatch(payload)
        fake_executor.run.assert_awaited_once()
        ctx = captured["ctx"]
        self.assertEqual(ctx.resume_session_id, "sX")
        self.assertEqual(ctx.resume_messages, payload["messages"])
        self.assertEqual(ctx.user_message, "do the thing")

    async def test_marks_failed_when_executor_raises(self):
        payload = {
            "session_id": "sErr", "bot_id": 99, "group_id": 1,
            "config": {}, "user_message": "x",
            "messages": [{"role": "user", "content": "x"}],
            "executor_id": "tool_loop_v1",
        }

        def boom():
            raise RuntimeError("kaboom")

        _, _, mock_status = await self._run_dispatch(payload, run_side_effect=boom)
        mock_status.assert_awaited_with("sErr", "failed")


# ---------------------------------------------------------------------------
# Resume branch in ToolLoopV1.run reuses the session and closes it (DFT-019)
# ---------------------------------------------------------------------------

class TestResumeRunClosesSession(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _db_mod.DB_PATH = _TEST_DB
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
            # agent_sessions.bot_id/group_id are real FKs (DFT-028); seed parents.
            await db.execute("INSERT OR IGNORE INTO groups (id, name) VALUES (1, 'g')")
            await db.execute(
                "INSERT OR IGNORE INTO members (id, group_id, name, type) "
                "VALUES (7, 1, 'bot', 'bot')"
            )
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def _count_sessions(self):
        import aiosqlite
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute("SELECT COUNT(*) FROM agent_sessions") as cur:
                (n,) = await cur.fetchone()
        return n

    async def test_resume_reuses_session_and_completes(self):
        import sessions
        from executors.plugins.tool_loop_v1 import ToolLoopV1
        from executors.base import ExecutionContext, ExecutionResult

        # Pre-seed a crashed session left in 'recovering'.
        sid = "resume-1"
        await sessions.create_session(
            session_id=sid, bot_id=7, group_id=1,
            config={"system_prompt": "orig", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="original task",
        )
        await sessions.update_session_status(sid, "recovering")

        # Reconstructed WAL: a completed write_file tool already ran before crash.
        resume_messages = [
            {"role": "system", "content": "orig"},
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "tc1", "type": "function",
                             "function": {"name": "write_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc1", "name": "write_file",
             "content": "已写入 out.txt"},
        ]

        captured_stream_msgs = {}

        async def fake_stream(system_prompt, messages, *a, **kw):
            captured_stream_msgs["msgs"] = messages
            captured_stream_msgs["system"] = system_prompt
            yield "final answer"

        async def fake_call_ai_once(*a, **kw):
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
            broadcaster=AsyncMock(),
            resume_session_id=sid,
            resume_messages=resume_messages,
        )

        m = "executors.plugins.tool_loop_v1."
        fake_db = MagicMock()
        fake_db.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db.__aexit__ = AsyncMock(return_value=False)

        with patch(m + "call_ai_stream_messages", new=fake_stream), \
             patch(m + "call_ai_once", new=fake_call_ai_once), \
             patch(m + "get_memory_context", new=AsyncMock(return_value="")), \
             patch(m + "list_skills_all", return_value=[]), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch("executors.plugins.tool_loop_v1.tool_executor.get_schemas",
                   return_value=[{"function": {"name": "write_file"}}]), \
             patch(m + "get_db", return_value=fake_db), \
             patch(m + "save_message", new=AsyncMock(return_value=123)), \
             patch(m + "get_messages", new=AsyncMock(return_value=[{"created_at": "now"}])), \
             patch(m + "add_to_chroma", new=AsyncMock()), \
             patch(m + "maybe_summarize", new=AsyncMock()), \
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
