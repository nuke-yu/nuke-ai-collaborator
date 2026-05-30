import os
import sys
import unittest
from pathlib import Path
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
import db.migrations as _migrations_mod

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_snapshot.db")

class TestSnapshot(unittest.IsolatedAsyncioTestCase):
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
            await db.execute("INSERT INTO members (id, group_id, name, type) VALUES (1, 1, 'b', 'bot')")
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_migration_008_exists(self):
        import aiosqlite
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute("PRAGMA table_info(agent_sessions)") as cur:
                cols = await cur.fetchall()
                col_names = [c[1] for c in cols]
        self.assertIn("last_snapshot_json", col_names)

    async def test_save_snapshot(self):
        from sessions.store import create_session, save_snapshot, get_session
        sid = "snap-1"
        await create_session(
            session_id=sid, bot_id=1, group_id=1,
            config={"p": "v"}, user_message="hello"
        )
        
        test_messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        await save_snapshot(sid, test_messages)
        
        session = await get_session(sid)
        self.assertEqual(json.loads(session["last_snapshot_json"]), test_messages)
        self.assertIsNotNone(session["updated_at"])

if __name__ == "__main__":
    unittest.main()
