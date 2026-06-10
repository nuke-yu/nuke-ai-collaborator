import unittest
import os
import sys
import shutil
import asyncio
import json
from pathlib import Path

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _db_writer
import workspace

# Configure test database and workspaces paths
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_chat.db")
TEST_WORKSPACES_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "test_workspaces"

database.DB_PATH = TEST_DB_PATH
# add_member 走 write_connect()，它的默认路径来自 db.writer.DB_PATH —— 这是与
# db.DB_PATH 不同的另一个模块全局；不一起重定向，写入会落进真实库（见 test_sessions）。
_db_writer.DB_PATH = TEST_DB_PATH
workspace.WORKSPACE_ROOT = TEST_WORKSPACES_ROOT
# 路径真相源：layout 实时读取 skills.constants.WORKSPACE_ROOT，必须同步重定向，
# 否则 init_bot_workspace 会把 bot 目录写进真实仓库目录。
import skills.constants as _skill_const
_skill_const.WORKSPACE_ROOT = TEST_WORKSPACES_ROOT

from main import app
from httpx import AsyncClient

class TestMemberRoutes(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Bypass DFT-082 auth so these tests exercise route logic (cleared in tearDown).
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        # Remove test db and workspace if they exist
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        if TEST_WORKSPACES_ROOT.exists():
            shutil.rmtree(TEST_WORKSPACES_ROOT)
            
        # Initialize test database tables
        await database.init_db()
        
        # Create a test group first (group_id is a foreign key in members)
        async with database.get_db() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db.commit()

    async def asyncTearDown(self):
        from core import auth as _auth
        app.dependency_overrides.pop(_auth.get_current_user, None)
        # Cleanup test db
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass
        # Cleanup test workspaces directory
        if TEST_WORKSPACES_ROOT.exists():
            try:
                shutil.rmtree(TEST_WORKSPACES_ROOT)
            except Exception:
                pass

    async def test_add_member_with_all_fields(self):
        """Test creating a bot member with all custom parameters (personality, temperature, executor configurations)."""
        async with AsyncClient(app=app, base_url="http://test") as ac:
            payload = {
                "name": "SuperBot",
                "type": "bot",
                "role": "QA Engineer",
                "system_prompt": "You are a QA Bot",
                "personality_prompt": "Strict and rigorous",
                "avatar_color": "#ff00ff",
                "model_provider": "claude",
                "model_name": "claude-sonnet-4-6",
                "temperature": 0.2,
                "max_tokens": 1024,
                "executor_id": "tool_loop_v1",
                "executor_config": {"mode": "safe", "allow_retries": True},
                "done_keyword": "FINISHED"
            }
            response = await ac.post("/api/groups/1/members", json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("id", data)
            bot_id = data["id"]
            import sqlite3 as _sq
            from db.context import current_db_path as _cdp
            print("DBG ctxvar=", _cdp.get(), "db.DB_PATH=", database.DB_PATH, "writer.DB_PATH=", _db_writer.DB_PATH, "bot_id=", bot_id)
            print("DBG test_chat.db members=", _sq.connect(TEST_DB_PATH).execute("SELECT id,name FROM members").fetchall())
            async with database.get_db() as _d:
                async with _d.execute("SELECT id,name FROM members") as _c:
                    print("DBG get_db ALL members=", await _c.fetchall())
            import aiosqlite as _ai
            _rc = await _ai.connect(TEST_DB_PATH)
            print("DBG raw aiosqlite members=", await (await _rc.execute("SELECT id,name FROM members")).fetchall())
            await _rc.close()

            # Now verify the database has all the fields populated correctly
            async with database.get_db() as db:
                db.row_factory = database.aiosqlite.Row
                async with db.execute("SELECT * FROM members WHERE id = ?", (bot_id,)) as cur:
                    row = await cur.fetchone()
                    
            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "SuperBot")
            self.assertEqual(row["type"], "bot")
            self.assertEqual(row["role"], "QA Engineer")
            self.assertEqual(row["system_prompt"], "You are a QA Bot")
            self.assertEqual(row["personality_prompt"], "Strict and rigorous")
            self.assertEqual(row["avatar_color"], "#ff00ff")
            self.assertEqual(row["model_provider"], "claude")
            self.assertEqual(row["model_name"], "claude-sonnet-4-6")
            self.assertEqual(row["temperature"], 0.2)
            self.assertEqual(row["max_tokens"], 1024)
            self.assertEqual(row["executor_id"], "tool_loop_v1")
            self.assertEqual(json.loads(row["executor_config"]), {"mode": "safe", "allow_retries": True})
            self.assertEqual(row["done_keyword"], "FINISHED")
            
            # Check if workspace folders and metadata files were generated
            bot_ws_dir = TEST_WORKSPACES_ROOT / f"bot_{bot_id}"
            self.assertTrue(bot_ws_dir.exists())
            self.assertTrue((bot_ws_dir / "skills").exists())
            self.assertTrue((bot_ws_dir / "logs").exists())

if __name__ == "__main__":
    unittest.main()
