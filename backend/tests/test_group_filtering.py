import os
import sys
import unittest
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from main import app

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DB = str(os.path.join(_HERE, "test_group_filtering.db"))

class TestGroupFiltering(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = db.DB_PATH
        db.DB_PATH = _TEST_DB
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await db.init_db()
        await db.init_central_db(_TEST_DB)

    async def asyncTearDown(self):
        db.DB_PATH = self._orig
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_get_groups_filters_coding_agent_jobs(self):
        # Insert normal group and coding agent job group
        async with db.connect(_TEST_DB) as conn:
            await conn.execute("INSERT INTO groups (name) VALUES ('General Group')")
            await conn.execute("INSERT INTO groups (name) VALUES ('Coding Agent: task_123')")
            await conn.commit()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Register user & login
            await client.post("/api/auth/register", json={"username": "user1", "password": "pass123"})
            login_resp = await client.post("/api/auth/login", json={"username": "user1", "password": "pass123"})
            token = login_resp.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}

            # 1. Default request should exclude Coding Agent: groups
            resp = await client.get("/api/groups", headers=headers)
            self.assertEqual(resp.status_code, 200)
            groups = resp.json()
            names = [g["name"] for g in groups]
            self.assertIn("General Group", names)
            self.assertNotIn("Coding Agent: task_123", names)

            # 2. include_jobs=true should include all groups
            resp = await client.get("/api/groups?include_jobs=true", headers=headers)
            self.assertEqual(resp.status_code, 200)
            all_groups = resp.json()
            all_names = [g["name"] for g in all_groups]
            self.assertIn("General Group", all_names)
            self.assertIn("Coding Agent: task_123", all_names)
