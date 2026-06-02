import os
import sys
import unittest
import asyncio
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
from core.orchestration.locks import get_active_bot, set_active_bot, release_lock

_HERE = Path(__file__).parent.parent if 'Path' in locals() else os.path.dirname(__file__)
_TEST_DB = str(os.path.join(os.path.dirname(_HERE), "test_locks.db"))

class TestDurableLocks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _db_mod.DB_PATH = _TEST_DB
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
            # Seed a group and bots
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'g')")
            await db.execute("INSERT INTO members (id, group_id, name, type) VALUES (10, 1, 'bot10', 'bot')")
            await db.execute("INSERT INTO members (id, group_id, name, type) VALUES (20, 1, 'bot20', 'bot')")
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_set_and_get_lock(self):
        # Initially no lock
        self.assertIsNone(await get_active_bot(1))
        
        # Set lock
        await set_active_bot(1, 10)
        self.assertEqual(await get_active_bot(1), 10)
        
        # Update lock (Upsert)
        await set_active_bot(1, 20)
        self.assertEqual(await get_active_bot(1), 20)

    async def test_release_lock(self):
        await set_active_bot(1, 10)
        self.assertEqual(await get_active_bot(1), 10)
        
        await release_lock(1)
        self.assertIsNone(await get_active_bot(1))

    async def test_orchestrator_integration(self):
        # The mention->lock routing that used to live in
        # core.orchestrator.select_triggered_bots is now inside
        # DeclarativeOrchestrator.dispatch (free-form chat branch).
        from core.orchestration.declarative import DeclarativeOrchestrator
        orch = DeclarativeOrchestrator()

        members = [
            {"id": 10, "name": "bot10", "role": "dev", "type": "bot"},
            {"id": 20, "name": "bot20", "role": "qa", "type": "bot"},
        ]

        async def triggered(content):
            step = await orch.dispatch(1, {"content": content}, members, [])
            return [u.bot for u in step.next_units]

        # Explicit mention selects that bot and sets the lock.
        t = await triggered("@bot10 hello")
        self.assertEqual([b["id"] for b in t], [10])
        self.assertEqual(await get_active_bot(1), 10)

        # Subsequent message without a mention follows the lock.
        t = await triggered("how are you?")
        self.assertEqual([b["id"] for b in t], [10])

        # Mentioning another bot switches the lock.
        t = await triggered("@bot20 your turn")
        self.assertEqual([b["id"] for b in t], [20])
        self.assertEqual(await get_active_bot(1), 20)

if __name__ == "__main__":
    unittest.main()
