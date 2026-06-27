"""Plan B Task 7 — external_skills registry CRUD."""
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


class TestRegistry(unittest.TestCase):
    def test_register_list_get_remove_and_dup(self):
        from skills import registry
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            rid = await registry.register(
                "deploy", "global", registry.GLOBAL_GROUP_ID,
                "https://github.com/x/y", "main", "abc123", "1.0.0",
                "posix", "run_shell", 42,
            )
            rows = await registry.list_external()
            got = await registry.get_external(rid)
            # Duplicate same (scope_kind, group_id, name) → ValueError
            dup_raised = False
            try:
                await registry.register("deploy", "global", registry.GLOBAL_GROUP_ID,
                                        "u", "r", "c", "v", "pure", "", 1)
            except ValueError:
                dup_raised = True
            removed = await registry.remove_external(rid)
            after = await registry.list_external()
            return rid, rows, got, dup_raised, removed, after

        try:
            _db.DB_PATH = path
            rid, rows, got, dup_raised, removed, after = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(got["name"], "deploy")
        self.assertEqual(got["commit_sha"], "abc123")
        self.assertEqual(got["platforms"], "posix")
        self.assertTrue(dup_raised)
        self.assertEqual(removed["id"], rid)
        self.assertEqual(after, [])


if __name__ == "__main__":
    unittest.main()
