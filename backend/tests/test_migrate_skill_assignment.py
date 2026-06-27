"""Plan C Task 1 — blanket run_skill rule detection + per-bot expansion plan."""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


class TestDetection(unittest.TestCase):
    def test_is_blanket_run_skill_rule(self):
        from scripts.migrate_skill_assignment import is_blanket_run_skill_rule
        from permissions.models import Rule
        self.assertTrue(is_blanket_run_skill_rule(Rule(tool_pattern="run_skill", args_pattern="", action="allow")))
        self.assertTrue(is_blanket_run_skill_rule(Rule(tool_pattern="run_skill*", args_pattern="", action="allow")))
        # name-scoped already → not blanket
        self.assertFalse(is_blanket_run_skill_rule(Rule(tool_pattern="run_skill", args_pattern="deploy", action="allow")))
        # deny → not in scope
        self.assertFalse(is_blanket_run_skill_rule(Rule(tool_pattern="run_skill", args_pattern="", action="deny")))
        # allow-all-tools wildcard → intentionally excluded
        self.assertFalse(is_blanket_run_skill_rule(Rule(tool_pattern="*", args_pattern="", action="allow")))


class TestPlanForBot(unittest.TestCase):
    def test_plan_expands_only_uncovered_skills(self):
        from scripts import migrate_skill_assignment as M
        from permissions import db as pdb

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def fake_list(bot_id, group_id=None, role=None):
            return [{"name": "deploy"}, {"name": "lint"}]

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (7,1,'dev','bot','developer')")
                await conn.commit()
            # one blanket rule + a pre-existing name-scoped allow for 'lint'
            await pdb.save_rule(7, "run_skill", "", "allow")
            await pdb.save_rule(7, "run_skill", "lint", "allow")
            with patch.object(M, "list_skills_all", new=fake_list):
                return await M.plan_for_bot(7, 1, "developer")

        try:
            _db.DB_PATH = path
            plan = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        self.assertEqual(len(plan["blanket_rule_ids"]), 1)
        self.assertEqual(plan["add_patterns"], ["deploy"])     # 'lint' already covered
        self.assertEqual(plan["skipped_existing"], ["lint"])


class TestApply(unittest.TestCase):
    def test_apply_expands_then_idempotent(self):
        from scripts import migrate_skill_assignment as M
        from permissions import db as pdb

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def fake_list(bot_id, group_id=None, role=None):
            return [{"name": "deploy"}, {"name": "lint"}]

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (7,1,'dev','bot','developer')")
                await conn.commit()
            await pdb.save_rule(7, "run_skill", "", "allow")   # blanket
            with patch.object(M, "list_skills_all", new=fake_list):
                first = await M.migrate(apply=True)
                rules_after = await pdb.load_rules(7)
                # second run is a clean no-op
                second = await M.migrate(apply=True)
            return first, rules_after, second

        try:
            _db.DB_PATH = path
            first, rules_after, second = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        # blanket gone; two name-scoped allows added
        patterns = sorted(r.args_pattern for r in rules_after if r.action == "allow")
        self.assertEqual(patterns, ["deploy", "lint"])
        self.assertFalse(any(r.args_pattern == "" for r in rules_after))
        self.assertEqual(first["total_added"], 2)
        self.assertEqual(first["total_deleted"], 1)
        # idempotent: nothing left to migrate
        self.assertEqual(second["bots"], [])
        self.assertEqual(second["total_added"], 0)


class TestDryRunNoWrite(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        from scripts import migrate_skill_assignment as M
        from permissions import db as pdb

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def fake_list(bot_id, group_id=None, role=None):
            return [{"name": "deploy"}]

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (7,1,'dev','bot','developer')")
                await conn.commit()
            await pdb.save_rule(7, "run_skill", "", "allow")
            with patch.object(M, "list_skills_all", new=fake_list):
                res = await M.migrate(apply=False)
            rules_after = await pdb.load_rules(7)
            return res, rules_after

        try:
            _db.DB_PATH = path
            res, rules_after = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        # plan computed but DB untouched (blanket still there, no new rules)
        self.assertEqual(len(res["bots"]), 1)
        self.assertEqual(res["bots"][0]["add_patterns"], ["deploy"])
        self.assertEqual(res["total_added"], 0)
        self.assertTrue(any(r.args_pattern == "" for r in rules_after))
        self.assertEqual(len([r for r in rules_after if r.args_pattern == "deploy"]), 0)


if __name__ == "__main__":
    unittest.main()
