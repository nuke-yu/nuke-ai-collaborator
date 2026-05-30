import unittest
from unittest.mock import AsyncMock, patch
import sys
import os
import asyncio

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import ai.memory as memory

# Use a test database to isolate tests
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_chat.db")
database.DB_PATH = TEST_DB_PATH

# Mock call_ai from ai_client
async def mock_call_ai(*args, **kwargs):
    return "This is a mock summary line 1\nThis is a mock summary line 2"

class TestMemorySummarization(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Clean up database file if exists
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
        
        # Create a test group and a bot
        async with database.get_db() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role, avatar_color) VALUES (2, 1, 'MemoryBot', 'bot', 'Summarizer', '#123456')"
            )
            await db.commit()

    async def asyncTearDown(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    @patch("ai.client.call_ai", new=mock_call_ai)
    async def test_maybe_summarize_lifecycle_and_constraints(self):
        """Test DFT-007 (group_id not null constraint) and DFT-008 (id > last_id SQL constraint)."""
        # 1. Insert 16 messages for MemoryBot (member_id = 2) to trigger summary (threshold = 15)
        async with database.get_db() as db:
            for i in range(1, 17):
                await db.execute(
                    "INSERT INTO messages (id, group_id, member_id, content) VALUES (?, 1, 2, ?)",
                    (i, f"Message content {i}")
                )
            await db.commit()

        # Run maybe_summarize. Since there are 16 messages (> 15 threshold), it should summarize the first 15 messages (IDs 1-15).
        # And it should write group_id = 1 into role_summaries (fixing DFT-007 NOT NULL constraint).
        await memory.maybe_summarize(group_id=1, bot_id=2, role="Summarizer", member_ids=[2])

        # Verify that the summary row was successfully created and contains the correct group_id and covered_through_id.
        async with database.get_db() as db:
            db.row_factory = database.aiosqlite.Row
            async with db.execute("SELECT * FROM role_summaries WHERE bot_id = 2") as cur:
                rows = await cur.fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["group_id"], 1)
        self.assertEqual(rows[0]["bot_id"], 2)
        self.assertEqual(rows[0]["role"], "Summarizer")
        self.assertEqual(rows[0]["summary"], "This is a mock summary line 1\nThis is a mock summary line 2")
        self.assertEqual(rows[0]["covered_through_id"], 15)

        # 2. Test DFT-008 database-level filtering.
        # Currently, last summarized message ID is 15. The remaining unsummarized message is ID 16 (1 message).
        # Let's insert 10 more messages (IDs 17 to 26). Total unsummarized messages = 1 + 10 = 11.
        async with database.get_db() as db:
            for i in range(17, 27):
                await db.execute(
                    "INSERT INTO messages (id, group_id, member_id, content) VALUES (?, 1, 2, ?)",
                    (i, f"Message content {i}")
                )
            await db.commit()

        # Run maybe_summarize. With 11 unsummarized messages (< 15 threshold), it should NOT create a new summary.
        await memory.maybe_summarize(group_id=1, bot_id=2, role="Summarizer", member_ids=[2])

        async with database.get_db() as db:
            async with db.execute("SELECT COUNT(*) FROM role_summaries WHERE bot_id = 2") as cur:
                count = (await cur.fetchone())[0]
        self.assertEqual(count, 1) # Still only 1 summary row

        # 3. Add 5 more messages (IDs 27 to 31) to reach 16 unsummarized messages (threshold is 15).
        async with database.get_db() as db:
            for i in range(27, 32):
                await db.execute(
                    "INSERT INTO messages (id, group_id, member_id, content) VALUES (?, 1, 2, ?)",
                    (i, f"Message content {i}")
                )
            await db.commit()

        # Run maybe_summarize again. It should now trigger the second summary, summarizing IDs 16 to 30.
        await memory.maybe_summarize(group_id=1, bot_id=2, role="Summarizer", member_ids=[2])

        async with database.get_db() as db:
            db.row_factory = database.aiosqlite.Row
            async with db.execute("SELECT * FROM role_summaries WHERE bot_id = 2 ORDER BY id ASC") as cur:
                rows = await cur.fetchall()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["covered_through_id"], 15)
        self.assertEqual(rows[1]["covered_through_id"], 30)

class TestMemorySilentFailureLogging(unittest.IsolatedAsyncioTestCase):
    """DFT-043: maybe_summarize / get_memory_context 不再 try/except: pass 静默吞错，
    改为记录日志但不阻断主流程。"""

    async def test_maybe_summarize_logs_on_error_and_does_not_raise(self):
        with patch("ai.memory.get_db", side_effect=RuntimeError("db-boom-summarize")):
            with self.assertLogs("ai.memory", level="ERROR") as cm:
                # member_ids 非空以越过早返回；get_db 抛错被捕获并记日志
                await memory.maybe_summarize(group_id=1, bot_id=2, role="r", member_ids=[2])
        self.assertTrue(
            any("db-boom-summarize" in line for line in cm.output),
            f"异常详情应进日志，实际: {cm.output}",
        )

    async def test_get_memory_context_logs_on_error_and_returns_string(self):
        # 隔离 chroma：retrieve_relevant 返回空，避免触发 embedding 模型下载
        with patch("ai.memory.retrieve_relevant", new=AsyncMock(return_value=[])), \
             patch("ai.memory.get_db", side_effect=RuntimeError("db-boom-context")):
            with self.assertLogs("ai.memory", level="ERROR") as cm:
                result = await memory.get_memory_context(bot_id=2, role="r", query="q")
        self.assertIsInstance(result, str)
        self.assertTrue(
            any("db-boom-context" in line for line in cm.output),
            f"异常详情应进日志，实际: {cm.output}",
        )


if __name__ == "__main__":
    unittest.main()
