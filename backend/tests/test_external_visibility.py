"""Plan B Task 6 — external skills filtered per-bot via bot_skills."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db


def _run(coro):
    return asyncio.run(coro)


class TestAvailableSkillsForBot(unittest.TestCase):
    def test_unassigned_external_hidden_assigned_visible(self):
        from skills import discovery
        from skills import assignment
        from db.schema_split import init_central_db

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        all_skills = [
            {"name": "write-spec", "layer": "system"},
            {"name": "deploy", "layer": "external_global"},
            {"name": "secret", "layer": "external_global"},
        ]

        async def fake_list(bot_id, group_id=None, role=None):
            return list(all_skills)

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type) VALUES (1,1,'dev','bot')")
                await conn.commit()
            await assignment.set_assignment(1, "deploy", "external_global", enabled=True)
            with patch.object(discovery, "list_skills_all", new=fake_list):
                return await discovery.available_skills_for_bot(1, group_id=1)

        try:
            _db.DB_PATH = path
            visible = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        names = {s["name"] for s in visible}
        self.assertEqual(names, {"write-spec", "deploy"})  # 'secret' filtered out


if __name__ == "__main__":
    unittest.main()
