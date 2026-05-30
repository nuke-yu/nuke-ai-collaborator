"""
tests/test_db_pragmas.py — DFT-028/029

Verify the unified connect helper applies the safety pragmas on every
connection: foreign key enforcement, WAL journal mode, and a busy_timeout
so concurrent writers wait instead of raising "database is locked".
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite
import db as _db


class TestConnectPragmas(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._tmp = tempfile.mkdtemp()
        self._path = os.path.join(self._tmp, "pragma.db")
        self._orig = _db.DB_PATH
        _db.DB_PATH = self._path

    async def asyncTearDown(self):
        _db.DB_PATH = self._orig

    async def _pragma(self, conn, name):
        async with conn.execute(f"PRAGMA {name}") as cur:
            row = await cur.fetchone()
        return row[0]

    async def test_get_db_enables_foreign_keys(self):
        async with _db.get_db() as conn:
            self.assertEqual(await self._pragma(conn, "foreign_keys"), 1)

    async def test_get_db_sets_busy_timeout(self):
        async with _db.get_db() as conn:
            self.assertEqual(await self._pragma(conn, "busy_timeout"), 5000)

    async def test_get_db_sets_wal_journal_mode(self):
        async with _db.get_db() as conn:
            mode = await self._pragma(conn, "journal_mode")
            self.assertEqual(str(mode).lower(), "wal")

    async def test_connect_accepts_explicit_path(self):
        other = os.path.join(self._tmp, "other.db")
        async with _db.connect(other) as conn:
            self.assertEqual(await self._pragma(conn, "foreign_keys"), 1)
        self.assertTrue(os.path.exists(other))

    async def test_foreign_keys_enforced_on_insert(self):
        async with _db.get_db() as conn:
            await conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            await conn.execute(
                "CREATE TABLE child (id INTEGER PRIMARY KEY, "
                "parent_id INTEGER REFERENCES parent(id))"
            )
            await conn.commit()
            with self.assertRaises(aiosqlite.IntegrityError):
                await conn.execute("INSERT INTO child (parent_id) VALUES (999)")
                await conn.commit()


if __name__ == "__main__":
    unittest.main()
