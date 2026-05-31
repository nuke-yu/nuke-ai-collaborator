"""
tests/test_permission_routes_authz.py — DFT-050 权限路由边界校验
"""
import os
import sys
import unittest
import asyncio
from unittest.mock import patch, AsyncMock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HERE = Path(__file__).parent.parent
TEST_DB_NAME = "test_permroutes.db"
TEST_DB_PATH = str(_HERE / TEST_DB_NAME)

import db as database
# Force the global DB path before importing app
database.DB_PATH = TEST_DB_PATH

from main import app
from httpx import AsyncClient

class TestPermissionRouteAuthz(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Ensure clean state
        if os.path.exists(TEST_DB_PATH):
            try: os.remove(TEST_DB_PATH)
            except: pass
            
        await database.init_db()
        async with database.get_db() as db:
            # Check if seeded by migrations (some seeds might happen)
            async with db.execute("SELECT COUNT(*) FROM groups WHERE id = 1") as cur:
                row = await cur.fetchone()
                if row[0] == 0:
                    await db.execute("INSERT INTO groups (id, name) VALUES (1, 'G')")
            
            await db.execute("INSERT OR REPLACE INTO members (id, group_id, name, type) VALUES (10, 1, 'Bot', 'bot')")
            await db.execute("INSERT OR REPLACE INTO members (id, group_id, name, type) VALUES (20, 1, 'Alice', 'human')")
            await db.commit()

    async def asyncTearDown(self):
        # We don't remove here to avoid locking issues between tests
        pass

    async def test_add_rule_rejects_nonexistent_member(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            r = await ac.post(
                "/api/members/9999/permissions", json={"tool_pattern": "*", "action": "allow"}
            )
        self.assertEqual(r.status_code, 404)

    async def test_add_rule_rejects_human_member(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            r = await ac.post(
                "/api/members/20/permissions", json={"tool_pattern": "*", "action": "allow"}
            )
        self.assertEqual(r.status_code, 403)

    async def test_add_rule_rejects_invalid_action(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            r = await ac.post(
                "/api/members/10/permissions",
                json={"tool_pattern": "*", "action": "rm -rf"},
            )
        self.assertEqual(r.status_code, 400)

    async def test_add_rule_allows_bot_member(self):
        with patch("permissions.routes.save_rule", new=AsyncMock(return_value=42)) as m:
            async with AsyncClient(app=app, base_url="http://test") as ac:
                r = await ac.post(
                    "/api/members/10/permissions",
                    json={"tool_pattern": "run_shell", "action": "deny"},
                )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], 42)
        m.assert_awaited_once()

    async def test_get_rules_rejects_nonexistent_member(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            r = await ac.get("/api/members/9999/permissions")
        self.assertEqual(r.status_code, 404)

    async def test_delete_rule_rejects_human_member(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            r = await ac.delete("/api/members/20/permissions/1")
        self.assertEqual(r.status_code, 403)

if __name__ == "__main__":
    unittest.main()
