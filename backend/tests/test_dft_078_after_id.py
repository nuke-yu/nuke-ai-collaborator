import asyncio
import os
import sys
import unittest
import httpx
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import db.writer as _writer
from main import app

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DB = str(os.path.join(_HERE, "test_after_id_unique.db"))

class TestAfterId(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # The app routes through db.DB_PATH, not DATABASE_URL. Isolate to a temp DB
        # so register/login don't pollute the real chat.db and this test is immune
        # to other tests' DB-path patching.
        self._orig = db.DB_PATH
        self._orig_writer = _writer.DB_PATH
        db.DB_PATH = _TEST_DB
        _writer.DB_PATH = _TEST_DB
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await db.init_db()
        await db.init_central_db(_TEST_DB)  # users table (register/login)

        # Create group and members
        async with db.connect() as conn:
            await conn.execute("INSERT OR IGNORE INTO groups (id, name) VALUES (1, 'Test Group')")
            await conn.execute("INSERT OR IGNORE INTO members (id, group_id, name, type) VALUES (1, 1, 'User', 'human')")
            
            # Insert 5 messages
            for i in range(1, 6):
                await conn.execute(
                    "INSERT OR IGNORE INTO messages (id, group_id, member_id, content, created_at) VALUES (?, 1, 1, ?, ?)",
                    (i, f"msg {i}", datetime.now().isoformat())
                )
            await conn.commit()

    async def asyncTearDown(self):
        db.DB_PATH = self._orig
        _writer.DB_PATH = self._orig_writer
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def _get_token(self, client):
        await client.post("/api/auth/register", json={"username": "user1", "password": "pw"})
        resp = await client.post("/api/auth/login", json={"username": "user1", "password": "pw"})
        return resp.json()["token"]

    async def test_get_messages_after_id(self):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Fetch messages after ID 3
            token = await self._get_token(client)
            resp = await client.get("/api/groups/1/messages?after_id=3", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            msgs = data["messages"]
            
            # Should have msg 4 and 5
            self.assertEqual(len(msgs), 2)
            self.assertEqual(msgs[0]["id"], 4)
            self.assertEqual(msgs[1]["id"], 5)
            
            # Order should be ASC for after_id to append them correctly in frontend
            # Wait, our queries.py implementation:
            # rows = await conn.execute("... ORDER BY m.id ASC LIMIT ?")
            # return [_row_to_msg(r) for r in rows]
            # Yes, msg 4 then msg 5.

if __name__ == "__main__":
    unittest.main()
