"""Regression: init_group_db must tolerate a LEGACY group DB whose tool_events
predates the L4 `compressed` column.

Bug: init_group_db ran `CREATE INDEX ... ON tool_events(..., compressed)` from
_GROUP_DDL before lifecycle's step-2 run_migrations could ADD the column. On a
legacy group DB the index DDL raised `sqlite3.OperationalError: no such column:
compressed`, aborting hydrate for EVERY group -> bots never start -> @bot silent.
The compressed-dependent index is now best-effort in init_group_db; the migration
adds the column + (re)builds the index.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


# tool_events as it existed BEFORE the L4 `compressed` column (mirrors a real
# pre-upgrade group DB on disk).
_LEGACY_TOOL_EVENTS = """CREATE TABLE tool_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    bot_id INTEGER,
    thread_id TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL,
    args_summary TEXT NOT NULL DEFAULT '',
    result_summary TEXT NOT NULL DEFAULT '',
    is_error INTEGER NOT NULL DEFAULT 0,
    files_touched TEXT NOT NULL DEFAULT '[]',
    command TEXT
)"""


class TestLegacyGroupHydrate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = tempfile.mktemp(suffix="_legacy_group.db")
        async with db.connect(self.path) as conn:
            await conn.execute(_LEGACY_TOOL_EVENTS)
            await conn.commit()

    async def asyncTearDown(self):
        await db.aclose_writer()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except OSError:
                pass

    async def test_init_group_db_tolerates_legacy_tool_events_without_compressed(self):
        # Before the fix this raised: no such column: compressed
        await db.init_group_db(self.path)  # must not raise

        # init completed: other group tables are present, and the legacy
        # tool_events row survived (CREATE TABLE IF NOT EXISTS was a no-op).
        async with db.connect(self.path) as conn:
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            tables = {r[0] for r in await cur.fetchall()}
        self.assertIn("messages", tables)
        self.assertIn("tool_events", tables)


if __name__ == "__main__":
    unittest.main()
