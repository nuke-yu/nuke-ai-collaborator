import asyncio
import os
import sys
import unittest
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from main import app
from core import auth

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DB = str(os.path.join(_HERE, "test_auth_routes_unique.db"))

class TestProtectedRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"
        await db.init_db()

    async def asyncTearDown(self):
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_protected_endpoint_requires_auth(self):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Access without token should fail
            resp = await client.get("/api/groups")
            self.assertEqual(resp.status_code, 401)
            
            # 2. Register a user
            resp = await client.post("/api/auth/register", json={
                "username": "tester_9f8234d4",
                "password": "password123"
            })
            self.assertEqual(resp.status_code, 200)
            
            # 3. Login
            resp = await client.post("/api/auth/login", json={
                "username": "tester_9f8234d4",
                "password": "password123"
            })
            self.assertEqual(resp.status_code, 200)
            token = resp.json()["token"]
            
            # 4. Access with token should succeed
            resp = await client.get("/api/groups", headers={
                "Authorization": f"Bearer {token}"
            })
            self.assertEqual(resp.status_code, 200)

if __name__ == "__main__":
    unittest.main()
