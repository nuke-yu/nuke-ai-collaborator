import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
import db.writer as _writer_mod
from db.schema import init_db

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_recovery_prompt.db")

class TestRecoveryPrompt(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        # sessions.store writes via the serialized writer (db.write_connect),
        # which resolves from db.writer.DB_PATH — patch it too or recover_all's
        # status/snapshot writes hit the real DB. (See test_smoke_dispatch.)
        self._orig_writer = _writer_mod.DB_PATH
        _db_mod.DB_PATH = _TEST_DB
        _writer_mod.DB_PATH = _TEST_DB
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        
        # Seed parents
        async with aiosqlite.connect(_TEST_DB) as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'g')")
            await db.execute("INSERT INTO members (id, group_id, name, type) VALUES (1, 1, 'WorkerBot', 'bot')")
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        _writer_mod.DB_PATH = self._orig_writer
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_recover_all_abandons_orphan(self):
        # Chat semantics: an interrupted ('running') session is DROPPED, not resumed.
        # recover_all marks it 'failed' and does NOT prompt the user / re-dispatch.
        from sessions.store import create_session, save_snapshot, get_session
        from sessions.recovery import recover_all

        sid = "rec-1"
        await create_session(
            session_id=sid, bot_id=1, group_id=1,
            config={"p": "v"}, user_message="do work"
        )
        await save_snapshot(sid, [{"role": "user", "content": "do work"}])

        with patch("ws_manager.manager.broadcast", new=AsyncMock()) as mock_broadcast:
            await recover_all()
            mock_broadcast.assert_not_awaited()   # no recovery_prompt

        session = await get_session(sid)
        self.assertEqual(session["status"], "failed")

    async def test_resume_session_dispatches_task(self):
        from sessions.store import create_session, save_snapshot, update_session_status
        from sessions.recovery import resume_session
        
        sid = "res-1"
        await create_session(
            session_id=sid, bot_id=1, group_id=1,
            config={"p": "v"}, user_message="do work"
        )
        await save_snapshot(sid, [{"role": "user", "content": "do work"}])
        await update_session_status(sid, "awaiting_recovery")
        
        with patch("sessions.recovery._dispatch_recovery", new=AsyncMock()) as mock_dispatch:
            success = await resume_session(sid)
            self.assertTrue(success)
            mock_dispatch.assert_called_once()
            
            # Check status
            from sessions.store import get_session
            session = await get_session(sid)
            self.assertEqual(session["status"], "recovering")

if __name__ == "__main__":
    unittest.main()
