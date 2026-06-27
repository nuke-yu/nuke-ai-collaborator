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


class TestAssignmentCRUD(unittest.TestCase):
    def _fresh_db(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return path

    def test_upsert_list_remove_and_enabled_set(self):
        from skills import assignment
        path = self._fresh_db()
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            # bot_skills.bot_id → members → groups: create the FK chain first.
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1, 'g')")
                await conn.execute(
                    "INSERT INTO members (id, group_id, name, type) "
                    "VALUES (1, 1, 'dev', 'bot')"
                )
                await conn.commit()

            await assignment.set_assignment(1, "deploy", "external_global",
                                            enabled=True, assigned_by=42)
            await assignment.set_assignment(1, "lint", "external_group",
                                            enabled=False)
            rows = await assignment.list_assignments(1)
            enabled = await assignment.enabled_skill_names(1)

            # Upsert: flip 'lint' to enabled, change nothing else.
            await assignment.set_assignment(1, "lint", "external_group", enabled=True)
            enabled_after = await assignment.enabled_skill_names(1)

            await assignment.remove_assignment(1, "deploy")
            rows_after = await assignment.list_assignments(1)
            return rows, enabled, enabled_after, rows_after

        try:
            _db.DB_PATH = path
            rows, enabled, enabled_after, rows_after = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        by_name = {r["skill_name"]: r for r in rows}
        self.assertEqual(by_name["deploy"]["pool"], "external_global")
        self.assertTrue(by_name["deploy"]["enabled"])
        self.assertFalse(by_name["lint"]["enabled"])
        self.assertEqual(enabled, {"deploy"})
        self.assertEqual(enabled_after, {"deploy", "lint"})
        self.assertEqual({r["skill_name"] for r in rows_after}, {"lint"})


class TestFilterVisible(unittest.TestCase):
    def test_external_filtered_by_enabled_others_passthrough(self):
        from skills import assignment
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        skills = [
            {"name": "write-spec", "layer": "system"},
            {"name": "code-review", "layer": "role"},
            {"name": "deploy", "layer": "external_global"},
            {"name": "lint", "layer": "external_group"},
            {"name": "secret", "layer": "external_global"},
        ]

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1, 'g')")
                await conn.execute(
                    "INSERT INTO members (id, group_id, name, type) VALUES (1,1,'dev','bot')"
                )
                await conn.commit()
            await assignment.set_assignment(1, "deploy", "external_global", enabled=True)
            await assignment.set_assignment(1, "lint", "external_group", enabled=True)
            # 'secret' is NOT assigned → must be filtered out.
            return await assignment.filter_visible(1, skills)

        try:
            _db.DB_PATH = path
            visible = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        names = {s["name"] for s in visible}
        self.assertEqual(names, {"write-spec", "code-review", "deploy", "lint"})

    def test_no_external_layers_does_no_db_work(self):
        from skills import assignment
        skills = [{"name": "x", "layer": "system"}, {"name": "y", "layer": "learned"}]
        # No external entries → must not touch the DB (DB_PATH points nowhere here).
        out = _run(assignment.filter_visible(999, skills))
        self.assertEqual(out, skills)


if __name__ == "__main__":
    unittest.main()
