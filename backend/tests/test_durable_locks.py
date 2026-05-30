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
        from core.orchestrator import select_triggered_bots
        
        all_bots = [
            {"id": 10, "name": "bot10", "role": "dev"},
            {"id": 20, "name": "bot20", "role": "qa"}
        ]
        
        # Test explicit mention sets lock
        triggered = await select_triggered_bots("@bot10 hello", all_bots, 1)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["id"], 10)
        self.assertEqual(await get_active_bot(1), 10)
        
        # Test subsequent message without mention follows lock
        triggered = await select_triggered_bots("how are you?", all_bots, 1)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["id"], 10)
        
        # Test mention other bot switches lock
        triggered = await select_triggered_bots("@bot20 your turn", all_bots, 1)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["id"], 20)
        self.assertEqual(await get_active_bot(1), 20)

if __name__ == "__main__":
    unittest.main()
