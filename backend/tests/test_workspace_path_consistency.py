import asyncio
import os
import shutil
import unittest
from pathlib import Path

import db as database
import db.writer as _db_writer
import skills.constants as _skill_const
from db.schema import init_db
from httpx import AsyncClient
from main import app
from core import auth as _auth
from workspace import layout, write_file, list_file_history, read_file_history_version


_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_workspace_paths.db")
_TEST_ROOT = _HERE / "test_workspaces_paths"


class TestWorkspacePathConsistency(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_PATH = _TEST_DB
        _db_writer.DB_PATH = _TEST_DB
        _skill_const.WORKSPACE_ROOT = _TEST_ROOT
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        if _TEST_ROOT.exists():
            shutil.rmtree(_TEST_ROOT)
        await init_db()
        async with database.get_db() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'G')")
            await db.execute("INSERT INTO members (id, group_id, name, type) VALUES (10, 1, 'Bot', 'bot')")
            await db.commit()

    async def asyncTearDown(self):
        app.dependency_overrides.pop(_auth.get_current_user, None)
        if os.path.exists(_TEST_DB):
            try:
                os.remove(_TEST_DB)
            except Exception:
                pass
        if _TEST_ROOT.exists():
            try:
                shutil.rmtree(_TEST_ROOT)
            except Exception:
                pass

    async def test_shared_file_history_uses_shared_workspace_root(self):
        result1 = await write_file(10, "docs/note.md", "v1", group_id=1)
        result2 = await write_file(10, "docs/note.md", "v2", group_id=1)
        self.assertTrue(result1.startswith("已写入"))
        self.assertTrue(result2.startswith("已写入"))

        versions = list_file_history(10, "docs/note.md", group_id=1)
        self.assertEqual(len(versions), 1)

        content = read_file_history_version(10, "docs/note.md", versions[0]["ts"], group_id=1)
        self.assertEqual(content, "v1")

    async def test_remove_member_deletes_nested_group_bot_workspace(self):
        bot_dir = layout.bot_dir(1, 10)
        bot_dir.mkdir(parents=True, exist_ok=True)
        (bot_dir / "AGENT.md").write_text("hello", encoding="utf-8")
        self.assertTrue(bot_dir.exists())

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete("/api/groups/1/members/10")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(bot_dir.exists())


if __name__ == "__main__":
    unittest.main()
