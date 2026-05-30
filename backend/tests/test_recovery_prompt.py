import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_recovery_prompt.db")

class TestRecoveryPrompt(unittest.IsolatedAsyncioTestCase):
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
        
        # Seed parents
        async with aiosqlite.connect(_TEST_DB) as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'g')")
            await db.execute("INSERT INTO members (id, group_id, name, type) VALUES (1, 1, 'WorkerBot', 'bot')")
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_recover_all_sends_prompt(self):
        from sessions.store import create_session, save_snapshot
        from sessions.recovery import recover_all
        
        sid = "rec-1"
        await create_session(
            session_id=sid, bot_id=1, group_id=1,
            config={"p": "v"}, user_message="do work"
        )
        await save_snapshot(sid, [{"role": "user", "content": "do work"}])
        
        with patch("ws_manager.manager.broadcast", new=AsyncMock()) as mock_broadcast:
            await recover_all()
            
            # Check if broadcast was called with recovery_prompt
            mock_broadcast.assert_awaited_once()
            args = mock_broadcast.call_args[0][1]
            self.assertEqual(args["type"], "recovery_prompt")
            self.assertEqual(args["session_id"], sid)
            self.assertEqual(args["bot_name"], "WorkerBot")

        # Check status
        from sessions.store import get_session
        session = await get_session(sid)
        self.assertEqual(session["status"], "awaiting_recovery")

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
