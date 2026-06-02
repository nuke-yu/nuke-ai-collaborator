import unittest
from unittest.mock import patch
import sys
import os
import asyncio

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_chat.db")
database.DB_PATH = TEST_DB_PATH

from main import app
from httpx import AsyncClient

class TestMessageTimestamps(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Bypass DFT-082 auth so these tests exercise route logic (cleared in tearDown).
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
        
        async with database.get_db() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role, avatar_color) VALUES (2, 1, 'UserBot', 'bot', 'QA', '#123456')"
            )
            # Insert a message with a specific timestamp that is timezone-free
            await db.execute(
                "INSERT INTO messages (id, group_id, member_id, content, created_at) VALUES (10, 1, 2, 'Test message', '2026-05-26 12:34:56')"
            )
            await db.commit()

    async def asyncTearDown(self):
        from core import auth as _auth
        app.dependency_overrides.pop(_auth.get_current_user, None)
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    async def test_get_messages_timestamp_formatting(self):
        """Verify that get_group_messages returns ISO 8601 UTC formatted timestamps (DFT-010)."""
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/groups/1/messages")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            messages = data.get("messages", [])
            self.assertEqual(len(messages), 1)
            # The original timestamp '2026-05-26 12:34:56' should be formatted to '2026-05-26T12:34:56Z'
            self.assertEqual(messages[0]["created_at"], "2026-05-26T12:34:56Z")

    async def test_search_messages_timestamp_formatting(self):
        """Verify that search_group_messages returns ISO 8601 UTC formatted timestamps (DFT-010)."""
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/groups/1/messages/search", params={"q": "Test"})
            self.assertEqual(response.status_code, 200)
            messages = response.json()
            self.assertEqual(len(messages), 1)
            # The original timestamp '2026-05-26 12:34:56' should be formatted to '2026-05-26T12:34:56Z'
            self.assertEqual(messages[0]["created_at"], "2026-05-26T12:34:56Z")

if __name__ == "__main__":
    unittest.main()
