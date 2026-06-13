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

from unittest.mock import AsyncMock, MagicMock, patch

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


class TestChromaMemoryEnhancements(unittest.IsolatedAsyncioTestCase):

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_add_to_chroma_includes_group_id_and_timestamp(self, mock_get_col, mock_call_once):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        # #1: 抽取走 call_ai_once（provider/model 透传），返回结构化 dict
        mock_call_once.return_value = {"type": "text", "content": "Hello world memory"}
        mock_col.query.return_value = {}

        await memory.add_to_chroma(
            message_id=42,
            content="Hello world memory",
            role="assistant",
            bot_id=5,
            group_id=9,
            provider="claude",
            model="claude-opus-4-8",
        )
        
        await asyncio.sleep(0.1)

        mock_col.upsert.assert_called_once()
        kwargs = mock_col.upsert.call_args[1]
        self.assertEqual(kwargs["ids"], ["42_0"])
        self.assertEqual(kwargs["documents"], ["Hello world memory"])
        self.assertEqual(kwargs["metadatas"][0]["bot_id"], 5)
        self.assertEqual(kwargs["metadatas"][0]["group_id"], 9)
        self.assertEqual(kwargs["metadatas"][0]["role"], "assistant")
        self.assertIn("timestamp", kwargs["metadatas"][0])
        # #1: 群组配置的 provider/model 必须透传给 LLM 调用，而非写死 deepseek
        self.assertEqual(mock_call_once.call_args[0][2], "claude")
        self.assertEqual(mock_call_once.call_args[0][3], "claude-opus-4-8")

    @patch("ai.memory._get_collection")
    async def test_retrieve_relevant_group_id_filter_and_recency_rerank(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        
        import time
        t_now = time.time()
        # Candidate 1: Doc A, dist = 0.1 (similarity = 0.9), timestamp = 10 days ago (old)
        # Candidate 2: Doc B, dist = 0.2 (similarity = 0.8), timestamp = 0.1 days ago (recent)
        # Candidate 3: Doc C, dist = 0.15 (similarity = 0.85), timestamp = 2 days ago (mid)
        mock_col.query.return_value = {
            "documents": [["Doc A", "Doc B", "Doc C"]],
            "metadatas": [[
                {"timestamp": t_now - 10.0 * 86400, "bot_id": 5, "group_id": 9},
                {"timestamp": t_now - 0.1 * 86400, "bot_id": 5, "group_id": 9},
                {"timestamp": t_now - 2.0 * 86400, "bot_id": 5, "group_id": 9}
            ]],
            "distances": [[0.1, 0.2, 0.15]],
            "ids": [["1", "2", "3"]]
        }
        
        results = await memory.retrieve_relevant(bot_id=5, group_id=9, query="test query", top_k=2)
        
        mock_col.query.assert_called_once()
        where_clause = mock_col.query.call_args[1]["where"]
        self.assertEqual(
            where_clause,
            {"$and": [{"bot_id": {"$eq": 5}}, {"group_id": {"$eq": 9}}]}
        )
        
        # Expected order: B, C, A. With top_k=2: [B, C]
        self.assertEqual(results, ["Doc B", "Doc C"])

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory.retrieve_relevant")
    @patch("ai.memory.get_db")
    async def test_get_memory_context_query_rewrite(self, mock_get_db, mock_retrieve, mock_call_once):
        mock_db = MagicMock()
        mock_get_db.return_value.__aenter__.return_value = mock_db
        mock_db.execute.return_value.__aenter__.return_value.fetchall = AsyncMock(return_value=[])

        mock_retrieve.return_value = ["Relevant memory"]

        history = [
            {"sender_name": "User", "sender_type": "human", "content": "I want to deploy to port 8080"},
            {"sender_name": "Bot", "sender_type": "bot", "content": "Sure, setting port to 8080"}
        ]

        result = await memory.get_memory_context(
            bot_id=5,
            role="assistant",
            query="发表你在本轮的观点",
            group_id=9,
            history=history
        )

        # #2: 模板化 trigger 在本地改写，热路径上不应再触发任何 LLM 调用
        mock_call_once.assert_not_called()
        # 改写为最近一条真人消息（真实话题），用它去检索
        mock_retrieve.assert_called_once_with(5, 9, "I want to deploy to port 8080")

    @patch("ai.memory._get_collection")
    async def test_delete_bot_memory(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        
        await memory.delete_bot_memory(bot_id=5, group_id=9)
        await asyncio.sleep(0.1)
        
        mock_col.delete.assert_called_once_with(
            where={"$and": [{"bot_id": {"$eq": 5}}, {"group_id": {"$eq": 9}}]}
        )

    @patch("ai.memory._get_collection")
    async def test_clear_bot_context_integration(self, mock_get_col):
        from db.queries import clear_bot_context
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
        
        async with database.get_db() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role, avatar_color) VALUES (2, 1, 'MemoryBot', 'bot', 'Summarizer', '#123456')"
            )
            await db.execute(
                "INSERT INTO role_summaries (bot_id, group_id, role, summary, covered_through_id) VALUES (2, 1, 'Summarizer', 'Summary', 10)"
            )
            await db.commit()
            
            await clear_bot_context(db, member_id=2, group_id=1)
            
            async with db.execute("SELECT COUNT(*) FROM role_summaries WHERE bot_id = 2") as cur:
                count = (await cur.fetchone())[0]
            self.assertEqual(count, 0)
            
            async with db.execute("SELECT context_cleared_at FROM members WHERE id = 2") as cur:
                cleared_at = (await cur.fetchone())[0]
            self.assertIsNotNone(cleared_at)
            
        await asyncio.sleep(0.1)
        mock_col.delete.assert_called_once_with(
            where={"$and": [{"bot_id": {"$eq": 2}}, {"group_id": {"$eq": 1}}]}
        )
        
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    @patch("ai.memory._get_collection")
    async def test_write_fact_redacts_secrets(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        
        memory.ChromaStore.write_fact_sync(
            f_id="test_key_redact",
            f_content="Setting API_KEY=sk-1234567890abcdef1234567890abcdef for deployment",
            metadata={"bot_id": 5}
        )
        
        mock_col.upsert.assert_called_once()
        kwargs = mock_col.upsert.call_args[1]
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef", kwargs["documents"][0])
        self.assertIn("[REDACTED]", kwargs["documents"][0])

    @patch("ai.memory._get_collection")
    async def test_prune_expired_memories(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        
        memory.ChromaStore.prune_expired_memories_sync(max_age_seconds=1000)
        
        mock_col.delete.assert_called_once()
        kwargs = mock_col.delete.call_args[1]
        self.assertIn("timestamp", kwargs["where"])
        self.assertIn("$lt", kwargs["where"]["timestamp"])

    @patch("ai.memory._get_collection")
    async def test_backfill_chroma_timestamps(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        
        mock_col.get.return_value = {
            "ids": ["12_0"],
            "metadatas": [{"bot_id": 5}]
        }
        
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
        
        async with database.get_db() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role, avatar_color) VALUES (5, 1, 'Bot', 'bot', 'r', '#123')"
            )
            await db.execute(
                "INSERT INTO messages (id, group_id, member_id, content, created_at) VALUES (12, 1, 5, 'Msg 12', '2026-06-12 12:00:00')"
            )
            await db.commit()
            
            await memory.backfill_chroma_timestamps()
            
        await asyncio.sleep(0.1)
        mock_col.update.assert_called_once()
        kwargs = mock_col.update.call_args[1]
        self.assertEqual(kwargs["ids"], ["12_0"])
        self.assertIn("timestamp", kwargs["metadatas"][0])
        self.assertGreater(kwargs["metadatas"][0]["timestamp"], 0)
        
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)


if __name__ == "__main__":
    unittest.main()
