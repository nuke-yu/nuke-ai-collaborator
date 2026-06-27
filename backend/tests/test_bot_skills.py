"""Plan A — Task 1/2/3: bot_skills + external_skills tables and assignment module."""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


class TestSkillTables(unittest.TestCase):
    def test_tables_and_columns_created(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        async def go():
            await init_central_db(path)
            async with _db.connect(path) as conn:
                cur = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                tables = {r[0] for r in await cur.fetchall()}
                cur = await conn.execute("PRAGMA table_info(bot_skills)")
                bs_cols = {r[1] for r in await cur.fetchall()}
                cur = await conn.execute("PRAGMA table_info(external_skills)")
                ex_cols = {r[1] for r in await cur.fetchall()}
            return tables, bs_cols, ex_cols

        try:
            tables, bs_cols, ex_cols = _run(go())
        finally:
            os.unlink(path)

        self.assertIn("bot_skills", tables)
        self.assertIn("external_skills", tables)
        self.assertEqual(
            bs_cols,
            {"id", "bot_id", "skill_name", "pool", "enabled", "assigned_by", "assigned_at"},
        )
        self.assertTrue(
            {"id", "name", "scope_kind", "group_id", "source_url", "ref",
             "commit_sha", "version", "platforms", "high_privilege",
             "imported_by", "imported_at", "status"}.issubset(ex_cols)
        )


if __name__ == "__main__":
    unittest.main()
