"""
tests/test_sessions.py — Agent session and session events migration tests

Covers:
  1. migration_004 — agent_sessions and session_events tables creation
  2. Schema structure validation
  3. Idempotency
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
import db.migrations as _migrations_mod

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_sessions.db")


def _use_test_db():
    _db_mod.DB_PATH = _TEST_DB


def _restore_db(orig):
    _db_mod.DB_PATH = orig


class TestMigration004(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import db.migrations as m
        self._orig_migrations = list(m.MIGRATIONS)

    async def asyncTearDown(self):
        import db.migrations as m
        m.MIGRATIONS = self._orig_migrations
        _restore_db(self._orig)
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_migration_004_creates_agent_sessions(self):
        import aiosqlite
        from db.migrations import migration_004, run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_sessions'"
            ) as cur:
                row = await cur.fetchone()
        self.assertIsNotNone(row)

    async def test_migration_004_creates_session_events(self):
        import aiosqlite
        from db.migrations import migration_004, run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_events'"
            ) as cur:
                row = await cur.fetchone()
        self.assertIsNotNone(row)

    async def test_migration_004_idempotent(self):
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        # Running again must not raise
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)


if __name__ == "__main__":
    unittest.main()
