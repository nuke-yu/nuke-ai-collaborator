import unittest
import sys
import os

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_chat.db")
database.DB_PATH = TEST_DB_PATH


class TestMessageMetaRoundtrip(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
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
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    async def test_message_meta_roundtrips(self):
        """save_message(meta=...) persists JSON and get_messages returns it parsed
        (carries the workflow confirm-gate card). Plain messages keep meta=None."""
        from db.queries import save_message, get_messages
        async with database.get_db() as db:
            gate_meta = {"kind": "confirm_gate", "gate_id": "1-0", "status": "pending"}
            await save_message(db, 1, 2, "确认需求", meta=gate_meta)
            msgs = await get_messages(db, 1, limit=10)
        gate_msg = next(m for m in msgs if m["content"] == "确认需求")
        self.assertEqual(gate_msg["meta"], gate_meta)
        plain = next(m for m in msgs if m["content"] == "Test message")
        self.assertIsNone(plain["meta"])


if __name__ == "__main__":
    unittest.main()
