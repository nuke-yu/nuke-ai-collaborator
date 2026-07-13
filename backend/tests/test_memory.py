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
                # thread_id 有值才会走摘要加载分支（即出错点）；自由聊天(None)按设计跳过摘要。
                result = await memory.get_memory_context(bot_id=2, role="r", query="q", thread_id="disc:t1")
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
        self.assertEqual(kwargs["ids"], ["fact_5_9_42_0"])
        self.assertEqual(kwargs["documents"], ["Hello world memory"])
        self.assertEqual(kwargs["metadatas"][0]["bot_id"], 5)
        self.assertEqual(kwargs["metadatas"][0]["group_id"], 9)
        self.assertEqual(kwargs["metadatas"][0]["role"], "assistant")
        self.assertIn("timestamp", kwargs["metadatas"][0])
        # 打分模型来源必须随事实落库，供将来换模型时按刻度归一化重标
        self.assertEqual(kwargs["metadatas"][0]["scored_by_model"], "claude/claude-opus-4-8")
        # #1: 群组配置的 provider/model 必须透传给 LLM 调用，而非写死 deepseek
        self.assertEqual(mock_call_once.call_args[0][2], "claude")
        self.assertEqual(mock_call_once.call_args[0][3], "claude-opus-4-8")

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_add_to_chroma_deletes_all_conflicting_ids(self, mock_get_col, mock_call_once):
        """冲突旧记忆 ID 必须全部按 ID 批量删除，与新事实条数无位置对应关系。

        回归：旧实现 del_ids[idx] 按 facts 下标配对，当冲突 ID 数 > 事实数时
        多出的 ID 永不删除，陈旧事实残留。
        """
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        # 召回 2 条相似旧事实，距离均 < 0.25 → 都进冲突候选池
        mock_col.query.return_value = {
            "documents": [["将服务端口修改为3000", "服务端口设置为5000"]],
            "ids": [["7_0", "9_1"]],
            "distances": [[0.1, 0.15]],
        }
        # 第 1 次调用：事实抽取（1 条事实）；第 2 次：批量冲突判定，返回 2 个旧 ID
        mock_call_once.side_effect = [
            {"type": "text", "content": "将服务端口修改为8080"},
            {"type": "text", "content": '["7_0", "9_1"]'},
        ]

        await memory.add_to_chroma(
            message_id=50,
            content="经过讨论，我们决定将服务端口从3000改为8080。",
            role="assistant",
            bot_id=5,
            group_id=9,
            provider="deepseek",
            model="deepseek-chat",
        )

        await asyncio.sleep(0.1)

        # 1 条新事实写入
        mock_col.upsert.assert_called_once()
        self.assertEqual(mock_col.upsert.call_args[1]["ids"], ["fact_5_9_50_0"])
        # 2 个冲突旧 ID 一次性批量删除（即便 > 事实条数）
        mock_col.delete.assert_any_call(ids=["7_0", "9_1"])

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_fact_ids_differ_across_groups_with_same_message_id(self, mock_get_col, mock_call_once):
        """Two groups with the same message_id must produce different fact IDs."""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_call_once.return_value = {"type": "text", "content": "fact"}
        mock_col.query.return_value = {}

        # Group A: message_id=10, bot_id=1, group_id=100
        await memory.add_to_chroma(
            message_id=10, content="这是一条足够长的测试内容用于验证", role="assistant",
            bot_id=1, group_id=100, provider="p", model="m")
        await asyncio.sleep(0.1)
        ids_group_a = mock_col.upsert.call_args[1]["ids"]

        mock_col.reset_mock()
        mock_call_once.return_value = {"type": "text", "content": "fact"}
        mock_col.query.return_value = {}

        # Group B: same message_id=10, different bot_id=2, group_id=200
        await memory.add_to_chroma(
            message_id=10, content="这是一条足够长的测试内容用于验证", role="assistant",
            bot_id=2, group_id=200, provider="p", model="m")
        await asyncio.sleep(0.1)
        ids_group_b = mock_col.upsert.call_args[1]["ids"]

        # IDs must be different despite same message_id
        self.assertNotEqual(ids_group_a, ids_group_b)
        self.assertEqual(ids_group_a, ["fact_1_100_10_0"])
        self.assertEqual(ids_group_b, ["fact_2_200_10_0"])

    @patch("ai.memory._get_collection")
    async def test_migrate_legacy_fact_ids_preserves_group_scope_and_newer_rows(self, mock_get_col):
        collection = MagicMock()
        mock_get_col.return_value = collection
        collection.get.return_value = {
            "ids": ["12_0", "13_0", "fact_5_9_13_0", "14_0"],
            "documents": ["group nine", "legacy duplicate", "newer", "unknown"],
            "metadatas": [
                {"bot_id": 5, "group_id": 9, "mem_type": "fact"},
                {"bot_id": 5, "group_id": 9, "mem_type": "fact"},
                {"bot_id": 5, "group_id": 9, "mem_type": "fact"},
                {"bot_id": 5, "mem_type": "fact"},
            ],
        }

        stats = await memory.migrate_legacy_chroma_fact_ids()

        self.assertEqual(stats, {
            "scanned": 4,
            "legacy": 3,
            "migrated": 1,
            "deduplicated": 1,
            "missing_scope": 1,
        })
        self.assertEqual(collection.upsert.call_args.kwargs["ids"], ["fact_5_9_12_0"])
        self.assertEqual(collection.upsert.call_args.kwargs["documents"], ["group nine"])
        collection.delete.assert_called_once_with(ids=["12_0", "13_0"])

    def test_scoped_fact_identity_rejects_legacy_and_keeps_group_dimension(self):
        self.assertEqual(memory.scoped_fact_identity("fact_5_9_12_0"), (9, 5, 12))
        self.assertEqual(memory.scoped_fact_identity("fact_5_10_12_0"), (10, 5, 12))
        self.assertIsNone(memory.scoped_fact_identity("12_0"))

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
        # WHERE 只按 bot/group 过滤，不再按 thread 硬过滤（thread 作用域改为 ranker 软加成）
        self.assertEqual(
            where_clause,
            {"$and": [{"bot_id": {"$eq": 5}}, {"group_id": {"$eq": 9}}]}
        )

        # Expected order: B, C, A. With top_k=2: [B, C]
        self.assertEqual(results, ["Doc B", "Doc C"])

    @patch("ai.memory._get_collection")
    async def test_retrieve_relevant_thread_soft_scoping_not_hard_filter(self, mock_get_col):
        """thread 作用域是软加成而非硬过滤：跨 thread 的相关事实仍可被召回（知识不按话题孤岛化），
        且 WHERE 不含任何 thread_id 条件。"""
        import time
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_col.query.return_value = {
            "documents": [["跨话题但相关的事实"]],
            "metadatas": [[{"timestamp": time.time(), "bot_id": 5, "group_id": 9, "thread_id": "disc:other"}]],
            "distances": [[0.1]],
            "ids": [["1"]]
        }
        results = await memory.retrieve_relevant(bot_id=5, group_id=9, query="test", top_k=3, thread_id="disc:current")
        mock_col.query.assert_called_once()
        where_clause = mock_col.query.call_args[1]["where"]
        # 不再按 thread 硬过滤
        self.assertEqual(
            where_clause,
            {"$and": [{"bot_id": {"$eq": 5}}, {"group_id": {"$eq": 9}}]}
        )
        # 跨 thread 的事实依然被返回（候选未被过滤掉）
        self.assertEqual(results, ["跨话题但相关的事实"])

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
        mock_retrieve.assert_called_once_with(5, 9, "I want to deploy to port 8080", thread_id=None)

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

        # P2: 差异化 TTL —— 事实清理排除反思($ne)，反思单独按更长 TTL 清理
        memory.ChromaStore.prune_expired_memories_sync(
            fact_max_age_seconds=1000, reflection_max_age_seconds=5000
        )

        self.assertEqual(mock_col.delete.call_count, 2)
        wheres = [c.kwargs["where"] for c in mock_col.delete.call_args_list]
        # 第一删：事实 + $ne reflection（绝不误删反思）
        fact_clauses = wheres[0]["$and"]
        self.assertIn({"mem_type": {"$ne": "reflection"}}, fact_clauses)
        # 第二删：仅 reflection
        refl_clauses = wheres[1]["$and"]
        self.assertIn({"mem_type": {"$eq": "reflection"}}, refl_clauses)

    @patch("ai.memory._get_collection")
    async def test_prune_uses_config_defaults(self, mock_get_col):
        """无参调用（add_to_chroma 的后台触发路径）应回退到 config 的 TTL。"""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        memory.ChromaStore.prune_expired_memories_sync()
        self.assertEqual(mock_col.delete.call_count, 2)

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

    @patch("ai.memory._get_collection")
    async def test_backfill_scored_by_model_stamps_missing_only(self, mock_get_col):
        """缺 scored_by_model 的条目打上 label；已有的跳过；保留其余 metadata。"""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_col.get.return_value = {
            "ids": ["1_0", "2_0", "refl_x"],
            "metadatas": [
                {"bot_id": 5, "importance": 0.9},               # 缺字段 → 应回填
                {"bot_id": 5, "scored_by_model": "claude/x"},   # 已有 → 应跳过
                {"bot_id": 6},                                   # 缺字段 → 应回填
            ],
        }

        stats = await memory.backfill_chroma_scored_by_model(label="legacy/unknown")

        self.assertEqual(stats, {"scanned": 3, "need": 2, "updated": 2})
        mock_col.update.assert_called_once()
        kwargs = mock_col.update.call_args[1]
        self.assertEqual(kwargs["ids"], ["1_0", "refl_x"])
        # 占位 label 落到两条缺字段记录上，且原有 metadata 不被破坏
        self.assertEqual(kwargs["metadatas"][0]["scored_by_model"], "legacy/unknown")
        self.assertEqual(kwargs["metadatas"][0]["importance"], 0.9)
        self.assertEqual(kwargs["metadatas"][1]["scored_by_model"], "legacy/unknown")
        self.assertEqual(kwargs["metadatas"][1]["bot_id"], 6)

    @patch("ai.memory._get_collection")
    async def test_backfill_scored_by_model_dry_run_writes_nothing(self, mock_get_col):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_col.get.return_value = {"ids": ["1_0"], "metadatas": [{"bot_id": 5}]}

        stats = await memory.backfill_chroma_scored_by_model(dry_run=True)

        self.assertEqual(stats, {"scanned": 1, "need": 1, "updated": 1})
        mock_col.update.assert_not_called()


class TestMemoryAuditFixes(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        import time
        self.time = time

    def test_similarity_floor_filtering(self):
        """测试相似度低于绝对下限（0.65）的内容不会被召回注入。"""
        from ai.memory import TimeDecayRanker
        ranker = TimeDecayRanker(similarity_floor=0.65)
        
        # Doc A: dist = 0.3 (similarity = 0.7 > 0.65)
        # Doc B: dist = 0.4 (similarity = 0.6 < 0.65)
        docs = ["Doc A", "Doc B"]
        metas = [{"timestamp": self.time.time(), "importance": 0.8}, {"timestamp": self.time.time(), "importance": 0.8}]
        dists = [0.3, 0.4]
        
        results = ranker.rank(docs, metas, dists, top_k=2)
        self.assertIn("Doc A", results)
        self.assertNotIn("Doc B", results)

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    async def test_salience_score_parsing(self, mock_call_once):
        """测试 FactExtractor.extract 能够正确解析带重要性得分的格式。"""
        from ai.memory import FactExtractor
        
        # 模拟 LLM 返回混合格式
        mock_call_once.return_value = {
            "type": "text",
            "content": "修改数据库配置|0.9\n偏好前端语言|0.7\n普通事实（无分数）"
        }
        
        # 传入 > 15 字符的文本以通过长度过滤
        facts = await FactExtractor.extract("经过讨论，我们需要将系统运行端口修改为8080")
        self.assertEqual(len(facts), 3)
        self.assertEqual(facts[0], ("修改数据库配置", 0.9))
        self.assertEqual(facts[1], ("偏好前端语言", 0.7))
        self.assertEqual(facts[2], ("普通事实（无分数）", 0.5))

    def test_three_factor_score_weighting(self):
        """测试 0.5 * sim + 0.3 * recency + 0.2 * importance 三因子公式和重要度排序。"""
        from ai.memory import TimeDecayRanker
        ranker = TimeDecayRanker(similarity_floor=0.0)
        
        # 保证时间基本一致，排除时间衰减干扰
        t_now = self.time.time()
        docs = ["Doc Low Importance", "Doc High Importance"]
        metas = [
            {"timestamp": t_now, "importance": 0.1},
            {"timestamp": t_now, "importance": 0.9}
        ]
        # 相似度一样
        dists = [0.2, 0.2]
        
        results = ranker.rank(docs, metas, dists, top_k=2)
        # 高重要度的分高，应该排第一
        self.assertEqual(results[0], "Doc High Importance")

    def test_thread_affinity_bonus(self):
        """同 thread 的事实获加性 bonus → 其它因子相等时排到跨 thread 事实之前；
        但跨 thread 事实仍是候选、仍会被返回（软加成，非硬过滤，知识不孤岛化）。"""
        from ai.memory import TimeDecayRanker
        import time as _t
        t_now = _t.time()
        ranker = TimeDecayRanker(similarity_floor=0.0, reflection_bonus=0.0, thread_affinity_bonus=0.2)
        docs = ["跨话题事实", "本话题事实"]
        metas = [
            {"timestamp": t_now, "importance": 0.5, "thread_id": "disc:other"},
            {"timestamp": t_now, "importance": 0.5, "thread_id": "disc:current"},
        ]
        dists = [0.2, 0.2]  # 相似度相同 → 仅 bonus 区分
        results = ranker.rank(docs, metas, dists, top_k=2, query="", thread_id="disc:current")
        self.assertEqual(results[0], "本话题事实")      # 同 thread 上浮
        self.assertIn("跨话题事实", results)             # 但跨 thread 仍被召回

    def test_thread_affinity_no_bonus_in_free_chat(self):
        """自由聊天（thread_id=None/空）没有当前话题可偏好 → 不施加 thread bonus，纯按相似度等因子。"""
        from ai.memory import TimeDecayRanker
        import time as _t
        t_now = _t.time()
        ranker = TimeDecayRanker(similarity_floor=0.0, reflection_bonus=0.0, thread_affinity_bonus=0.2)
        docs = ["低相似", "高相似"]
        metas = [
            {"timestamp": t_now, "importance": 0.5, "thread_id": ""},
            {"timestamp": t_now, "importance": 0.5, "thread_id": "disc:x"},
        ]
        dists = [0.4, 0.1]  # 第二条相似度更高
        results = ranker.rank(docs, metas, dists, top_k=2, query="", thread_id=None)
        self.assertEqual(results[0], "高相似")  # 无 thread bonus，相似度更高者排前

    @patch("ai.memory._get_collection")
    def test_get_unconsolidated_memories_per_thread_precise_fetch(self, mock_get_col):
        """#3: 反思抓取按各 thread 自己的水位线精确取增量——已知 thread 只取其水位线之后、
        未知 thread 取全部、自由聊天('')整体排除。杜绝被某沉寂 thread 的低水位线钉住、
        每轮重抓已消费事实致抓取窗口膨胀。"""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        memory.ChromaStore.get_unconsolidated_memories_sync(5, 9, {"disc:a": 100.0, "disc:b": 200.0})
        where = mock_col.get.call_args[1]["where"]
        conds = where["$and"]
        self.assertIn({"bot_id": {"$eq": 5}}, conds)
        self.assertIn({"group_id": {"$eq": 9}}, conds)
        or_clause = next(c for c in conds if "$or" in c)["$or"]
        # 每个已知 thread 只取其水位线之后
        self.assertIn({"$and": [{"thread_id": {"$eq": "disc:a"}}, {"timestamp": {"$gt": 100.0}}]}, or_clause)
        self.assertIn({"$and": [{"thread_id": {"$eq": "disc:b"}}, {"timestamp": {"$gt": 200.0}}]}, or_clause)
        # 未知 thread 取全部，且自由聊天 "" 一并排除
        nin = next(c for c in or_clause if "$nin" in c.get("thread_id", {}))
        self.assertEqual(sorted(nin["thread_id"]["$nin"]), ["", "disc:a", "disc:b"])

    @patch("ai.memory._get_collection")
    def test_get_unconsolidated_memories_cold_start_excludes_free_chat(self, mock_get_col):
        """无任何水位线（冷启动）：取全部非自由聊天事实。"""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        memory.ChromaStore.get_unconsolidated_memories_sync(5, 9, {})
        conds = mock_col.get.call_args[1]["where"]["$and"]
        self.assertIn({"thread_id": {"$ne": ""}}, conds)

    def test_keyword_boosting(self):
        """测试混合检索关键字精确匹配加分。"""
        from ai.memory import TimeDecayRanker
        ranker = TimeDecayRanker(similarity_floor=0.0)
        
        t_now = self.time.time()
        docs = ["database connection pool config on port 8080", "regular memory entry without keywords"]
        metas = [
            {"timestamp": t_now, "importance": 0.5},
            {"timestamp": t_now, "importance": 0.5}
        ]
        # 相似度完全一样，且无关键字加分时两者分值应当一样。查询 "port 8080" 应该给第一条加分。
        dists = [0.3, 0.3]
        
        results = ranker.rank(docs, metas, dists, top_k=2, query="find port 8080 settings")
        self.assertEqual(results[0], "database connection pool config on port 8080")

    def test_reflection_retrieval_bonus(self):
        """P2: 同等相似度/新近度/重要性下，反思洞察因 bonus 排在事实之前。"""
        from ai.memory import TimeDecayRanker
        import time as _t
        ranker = TimeDecayRanker(similarity_floor=0.0, reflection_bonus=0.1)
        t_now = _t.time()
        docs = ["普通事实记录", "高层反思洞察"]
        metas = [
            {"timestamp": t_now, "importance": 0.5, "mem_type": "fact"},
            {"timestamp": t_now, "importance": 0.5, "mem_type": "reflection"},
        ]
        dists = [0.3, 0.3]  # 相似度相同
        results = ranker.rank(docs, metas, dists, top_k=2)
        self.assertEqual(results[0], "高层反思洞察")

    # ── 巩固层 reflection (P1) ──────────────────────────────────────────
    @patch("ai.memory._set_reflection_watermark", new_callable=AsyncMock)
    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_maybe_reflect_generates_insight(self, mock_get_col, mock_call_once, mock_wm_get, mock_wm_set):
        import time as _t
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_wm_get.return_value = {}
        t_now = _t.time()
        # 5 条事实，importance 0.7 → 数量(5)≥阈值、Σ=3.5≥3.0 → 触发
        mock_col.get.return_value = {
            "ids": [f"{i}_0" for i in range(1, 6)],
            "documents": [f"部署第{i}次因配置失败" for i in range(1, 6)],
            "metadatas": [{"mem_type": "fact", "importance": 0.7, "timestamp": t_now, "thread_id": "test_thread"} for _ in range(5)],
        }
        mock_col.query.return_value = {}  # resolve_batch 无冲突候选
        mock_call_once.return_value = {"type": "text", "content": "项目反复因配置不一致部署失败，配置管理薄弱|0.9"}

        await memory.maybe_reflect(group_id=9, bot_id=5, role="dev", provider="claude", model="claude-opus-4-8")
        await asyncio.sleep(0.1)

        mock_call_once.assert_called_once()
        # provider/model 透传
        self.assertEqual(mock_call_once.call_args[0][2], "claude")
        # 洞察以 mem_type=reflection 写回，带 importance 与 provenance
        mock_col.upsert.assert_called_once()
        meta = mock_col.upsert.call_args[1]["metadatas"][0]
        self.assertEqual(meta["mem_type"], "reflection")
        self.assertEqual(meta["importance"], 0.9)
        self.assertIn("1_0", meta["source_ids"])
        # 水位线推进到最新事实 ts
        mock_wm_set.assert_awaited_once()
        self.assertEqual(mock_wm_set.await_args[0][2], "test_thread")
        self.assertAlmostEqual(mock_wm_set.await_args[0][3], t_now, places=3)

    @patch("ai.memory._set_reflection_watermark", new_callable=AsyncMock)
    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_maybe_reflect_below_threshold_no_llm(self, mock_get_col, mock_call_once, mock_wm_get, mock_wm_set):
        import time as _t
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_wm_get.return_value = {}
        # 只有 2 条事实 < REFLECT_MIN_FACTS(5) → 不触发
        mock_col.get.return_value = {
            "ids": ["1_0", "2_0"],
            "documents": ["事实一", "事实二"],
            "metadatas": [{"mem_type": "fact", "importance": 0.9, "timestamp": _t.time(), "thread_id": "test_thread"} for _ in range(2)],
        }
        await memory.maybe_reflect(group_id=9, bot_id=5, role="dev")
        await asyncio.sleep(0.05)

        mock_call_once.assert_not_called()
        mock_col.upsert.assert_not_called()
        mock_wm_set.assert_not_awaited()

    @patch("ai.memory._set_reflection_watermark", new_callable=AsyncMock)
    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_maybe_reflect_excludes_existing_reflections(self, mock_get_col, mock_call_once, mock_wm_get, mock_wm_set):
        """单层防失控：候选里的 mem_type=reflection 不计入事实，避免反思的反思。"""
        import time as _t
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_wm_get.return_value = {}
        t_now = _t.time()
        # 4 条事实 + 1 条已有反思；排除反思后只剩 4 < 5 → 不触发
        metas = [{"mem_type": "fact", "importance": 0.9, "timestamp": t_now, "thread_id": "test_thread"} for _ in range(4)]
        metas.append({"mem_type": "reflection", "importance": 0.9, "timestamp": t_now, "thread_id": "test_thread"})
        mock_col.get.return_value = {
            "ids": [f"{i}_0" for i in range(4)] + ["refl_5_9_1_0"],
            "documents": [f"事实{i}" for i in range(4)] + ["既有洞察"],
            "metadatas": metas,
        }
        await memory.maybe_reflect(group_id=9, bot_id=5, role="dev")
        await asyncio.sleep(0.05)

        mock_call_once.assert_not_called()

    @patch("core.config.REFLECT_MULTILEVEL", True)
    @patch("ai.memory._set_reflection_watermark", new_callable=AsyncMock)
    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_maybe_reflect_multilevel_includes_reflections(self, mock_get_col, mock_call_once, mock_wm_get, mock_wm_set):
        """P3 多层开启：纳入未到顶的 level-1 反思，新反思 level=2，provenance 指向它。"""
        import time as _t
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_wm_get.return_value = {}
        t_now = _t.time()
        # 4 条 level-0 事实 + 1 条 level-1 反思 = 5 条消费；多层下反思被纳入
        metas = [{"mem_type": "fact", "importance": 0.7, "timestamp": t_now, "thread_id": "test_thread"} for _ in range(4)]
        metas.append({"mem_type": "reflection", "level": 1, "importance": 0.9, "timestamp": t_now, "thread_id": "test_thread"})
        mock_col.get.return_value = {
            "ids": [f"{i}_0" for i in range(4)] + ["refl_5_9_1_0"],
            "documents": [f"事实{i}" for i in range(4)] + ["一层洞察"],
            "metadatas": metas,
        }
        mock_col.query.return_value = {}
        mock_call_once.return_value = {"type": "text", "content": "更高层的元洞察|0.95"}

        await memory.maybe_reflect(group_id=9, bot_id=5, role="dev", provider="claude", model="x")
        await asyncio.sleep(0.1)

        mock_call_once.assert_called_once()
        mock_col.upsert.assert_called_once()
        meta = mock_col.upsert.call_args[1]["metadatas"][0]
        self.assertEqual(meta["level"], 2)            # max(consumed level=1)+1
        self.assertIn("refl_5_9_1_0", meta["source_ids"])  # 链接指向下层反思

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_add_to_chroma_stamps_thread_id(self, mock_get_col, mock_call_once):
        """ingest 必须把当前讨论 topic（thread_id）打进事实 metadata，供反思按话题分区。"""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_call_once.return_value = {"type": "text", "content": "某条事实"}
        mock_col.query.return_value = {}

        await memory.add_to_chroma(
            message_id=7, content="某条事实内容很长足够抽取", role="dev",
            bot_id=5, group_id=9, provider="claude", model="x", thread_id="disc:topicA",
        )
        await asyncio.sleep(0.05)

        mock_col.upsert.assert_called_once()
        self.assertEqual(mock_col.upsert.call_args[1]["metadatas"][0]["thread_id"], "disc:topicA")

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_add_to_chroma_free_chat_thread_id_empty(self, mock_get_col, mock_call_once):
        """自由聊天（thread_id=None）落库为空串（Chroma metadata 不接受 None）。"""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_call_once.return_value = {"type": "text", "content": "某条事实"}
        mock_col.query.return_value = {}

        await memory.add_to_chroma(
            message_id=8, content="某条事实内容很长足够抽取", role="dev",
            bot_id=5, group_id=9, provider="claude", model="x",
        )
        await asyncio.sleep(0.05)

        self.assertEqual(mock_col.upsert.call_args[1]["metadatas"][0]["thread_id"], "")

    @patch("ai.memory._set_reflection_watermark", new_callable=AsyncMock)
    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_maybe_reflect_partitions_by_thread_no_bridging(
        self, mock_get_col, mock_call_once, mock_wm_get, mock_wm_set
    ):
        """核心修复：两个无关话题各自满阈值时，必须分别反思——绝不把两话题的事实
        揉进同一次 LLM 归纳（杜绝"桥梁洞察"）；产出的反思按各自 thread_id 落库。"""
        import time as _t
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_wm_get.return_value = {}
        t_now = _t.time()
        # 话题 A：5 条芒果健康事实；话题 B：5 条选股事实。各自 Σimportance=3.5≥3、条数=5≥5
        metas, ids, docs = [], [], []
        for i in range(5):
            metas.append({"mem_type": "fact", "importance": 0.7, "timestamp": t_now, "thread_id": "disc:mango"})
            ids.append(f"a{i}_0"); docs.append(f"芒果有益健康事实{i}")
        for i in range(5):
            metas.append({"mem_type": "fact", "importance": 0.7, "timestamp": t_now, "thread_id": "disc:stock"})
            ids.append(f"b{i}_0"); docs.append(f"选股策略事实{i}")
        mock_col.get.return_value = {"ids": ids, "documents": docs, "metadatas": metas}
        mock_col.query.return_value = {}
        mock_call_once.return_value = {"type": "text", "content": "话题内洞察|0.9"}

        await memory.maybe_reflect(group_id=9, bot_id=5, role="dev", provider="claude", model="x")
        await asyncio.sleep(0.1)

        # 每个话题各一次 LLM 归纳：两次调用，且每次的 prompt 只含单一话题的事实
        self.assertEqual(mock_call_once.call_count, 2)
        for call in mock_call_once.call_args_list:
            prompt = call[0][0]
            has_mango = "芒果" in prompt
            has_stock = "选股" in prompt
            self.assertTrue(has_mango != has_stock, "一次归纳不得同时包含两个话题的事实")

        # 两条反思分别按 thread_id 落库
        thread_ids = {c.kwargs["metadatas"][0]["thread_id"] for c in mock_col.upsert.call_args_list}
        self.assertEqual(thread_ids, {"disc:mango", "disc:stock"})

    @patch("ai.memory._set_reflection_watermark", new_callable=AsyncMock)
    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_maybe_reflect_prevents_starvation_of_subthreshold_thread(
        self, mock_get_col, mock_call_once, mock_wm_get, mock_wm_set
    ):
        """防止饥饿（独立推进水位线）：满阈值话题 A 触发归纳后，其水位线独立推进；
        未满阈值的话题 B 水位线不推进，事实不被丢弃/永久饥饿。"""
        import time as _t
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_wm_get.return_value = {}
        t_now = _t.time()
        ids, docs, metas = [], [], []
        for i in range(5):  # 话题 A 满阈值
            metas.append({"mem_type": "fact", "importance": 0.7, "timestamp": t_now, "thread_id": "disc:a"})
            ids.append(f"a{i}_0"); docs.append(f"A话题事实{i}")
        # 话题 B 只有 1 条（最新 ts），不满阈值
        t_late = t_now + 10
        metas.append({"mem_type": "fact", "importance": 0.9, "timestamp": t_late, "thread_id": "disc:b"})
        ids.append("b0_0"); docs.append("B话题孤立事实")
        mock_col.get.return_value = {"ids": ids, "documents": docs, "metadatas": metas}
        mock_col.query.return_value = {}
        mock_call_once.return_value = {"type": "text", "content": "A话题洞察|0.9"}

        await memory.maybe_reflect(group_id=9, bot_id=5, role="dev", provider="claude", model="x")
        await asyncio.sleep(0.1)

        # 只反思满阈值的话题 A（一次 LLM），B 不归纳
        mock_call_once.assert_called_once()
        self.assertIn("A话题", mock_call_once.call_args[0][0])
        self.assertNotIn("B话题", mock_call_once.call_args[0][0])
        # A 的水位线独立推进，B 的水位线不推进（mock_wm_set 仅被调用 1 次，且为 disc:a）
        mock_wm_set.assert_awaited_once()
        self.assertEqual(mock_wm_set.await_args[0][2], "disc:a")
        self.assertAlmostEqual(mock_wm_set.await_args[0][3], t_now, places=3)

    @patch("ai.memory._get_collection")
    async def test_get_memory_links_traverses_provenance(self, mock_get_col):
        """P3: get_memory_links 沿 source_ids 走一跳取回来源记忆。"""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col

        def _get(ids=None, include=None, where=None):
            if ids == ["refl_x"]:
                return {"ids": ["refl_x"], "documents": ["洞察"],
                        "metadatas": [{"mem_type": "reflection", "source_ids": "1_0,2_0"}]}
            if ids == ["1_0", "2_0"]:
                return {"ids": ["1_0", "2_0"], "documents": ["事实A", "事实B"],
                        "metadatas": [{"mem_type": "fact"}, {"mem_type": "fact"}]}
            return {}
        mock_col.get.side_effect = _get

        links = await memory.get_memory_links("refl_x")
        self.assertEqual([l["id"] for l in links], ["1_0", "2_0"])
        self.assertEqual(links[0]["document"], "事实A")
        self.assertEqual(links[0]["mem_type"], "fact")

    async def test_memory_db_read_write_symmetric_under_binding(self):
        """回归：分库 + 群已绑定时，水位线读写必须落在同一个(绑定的群)库。

        旧实现写端写死 central_db_path、读端走 get_db()(contextvar)，二者分叉——
        写进中央库、读自群库 → 往返失败。此测试在 bind_db 下真实往返来锁住对称性。
        """
        import tempfile, os as _os
        from db.context import bind_db
        from db.schema_split import init_group_db
        from db.migrations import run_migrations
        import db as _db

        # TemporaryDirectory 递归清理（连 SQLite 的 -wal/-shm 一起），且初始化也在保护内
        with tempfile.TemporaryDirectory() as d:
            gpath = _os.path.join(d, "chat.db")
            await init_group_db(gpath)
            async with _db.connect(gpath) as c:
                await run_migrations(c)

            memory._table_presence_cache.clear()  # 让其在绑定下重新探测
            try:
                with bind_db(gpath):
                    await memory._set_reflection_watermark(5, 1, "test_thread", 123.5)
                    got = await memory._get_reflection_watermark(5, 1, "test_thread")
                self.assertEqual(got, 123.5)  # 写入即可读回（同库）
                # 且确实落在绑定的群库
                async with _db.connect(gpath) as c:
                    async with c.execute(
                        "SELECT covered_through_ts FROM reflection_state WHERE bot_id=5 AND group_id=1 AND thread_id='test_thread'"
                    ) as cur:
                        row = await cur.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], 123.5)
            finally:
                memory._table_presence_cache.clear()

    def test_keyword_boost_alnum_only_for_mixed_query(self):
        """#1: 关键字增强只认英数。中文混合查询中的端口号 8080 仍应给含它的文档加分。"""
        from ai.memory import TimeDecayRanker
        import time as _t
        ranker = TimeDecayRanker(similarity_floor=0.0)
        t_now = _t.time()
        docs = ["将服务端口改为8080", "一段无关的中文记忆内容"]
        metas = [
            {"timestamp": t_now, "importance": 0.5, "mem_type": "fact"},
            {"timestamp": t_now, "importance": 0.5, "mem_type": "fact"},
        ]
        dists = [0.3, 0.3]  # 相似度相同，差异只来自关键字 8080
        results = ranker.rank(docs, metas, dists, top_k=2, query="怎么把服务端口改成8080")
        self.assertEqual(results[0], "将服务端口改为8080")

    @patch("ai.memory._set_reflection_watermark", new_callable=AsyncMock)
    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    @patch("core.config.REFLECT_MAX_BACKLOG", 4)
    async def test_maybe_reflect_force_advances_watermark_on_backlog(
        self, mock_get_col, mock_call_once, mock_wm_get, mock_wm_set
    ):
        """#2: 大量低重要性事实始终不触发时，超过积压上限要强制推进水位线（不空跑 LLM）。"""
        import time as _t
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_wm_get.return_value = {}
        t_now = _t.time()
        # 6 条 importance=0.1 的事实：数量≥MIN(5)、Σ=0.6<3.0、且 >MAX_BACKLOG(4)
        mock_col.get.return_value = {
            "ids": [f"{i}_0" for i in range(6)],
            "documents": [f"琐碎事实{i}" for i in range(6)],
            "metadatas": [{"mem_type": "fact", "importance": 0.1, "timestamp": t_now, "thread_id": "test_thread"} for _ in range(6)],
        }
        await memory.maybe_reflect(group_id=9, bot_id=5, role="dev")
        await asyncio.sleep(0.05)

        mock_call_once.assert_not_called()        # 未触发归纳
        mock_wm_set.assert_awaited_once()         # 但水位线被强制推进
        self.assertEqual(mock_wm_set.await_args[0][2], "test_thread")
        self.assertAlmostEqual(mock_wm_set.await_args[0][3], t_now, places=3)

    @patch("ai.memory._get_collection")
    async def test_backfill_chunks_large_in_clause(self, mock_get_col):
        """#3: 单群缺 timestamp 的消息 >999 时，IN(...) 必须分批，不能触发 too many SQL variables。"""
        import tempfile, os as _os
        from unittest.mock import patch as _patch
        import db as _db
        from db.schema_split import init_group_db
        from db.migrations import run_migrations

        N = 1100
        with tempfile.TemporaryDirectory() as d:
            gp = _os.path.join(d, "chat.db")
            await init_group_db(gp)
            async with _db.connect(gp) as c:
                await run_migrations(c)
                for i in range(1, N + 1):
                    await c.execute(
                        "INSERT INTO messages (id, group_id, member_id, content, created_at) "
                        "VALUES (?, 7, 1, ?, '2026-06-12 12:00:00')",
                        (i, f"m{i}"),
                    )
                await c.commit()

            mock_col = MagicMock()
            mock_get_col.return_value = mock_col
            # 1100 条无 timestamp、带 group_id 的记忆 → 全部需要回填
            mock_col.get.return_value = {
                "ids": [str(i) for i in range(1, N + 1)],
                "metadatas": [{"bot_id": 1, "group_id": 7} for _ in range(N)],
            }
            with _patch("runtime.dbpaths.group_db_path", return_value=gp):
                stats = await memory.backfill_chroma_timestamps(dry_run=False)
            self.assertEqual(stats["updated"], N)   # 全部回填，未因 IN 占位符超限崩溃
            mock_col.update.assert_called()

    def test_rank_tolerates_bad_timestamp_and_importance(self):
        """#1: 旧数据里 timestamp/importance 是字符串或缺失时，rank 不得抛 TypeError。"""
        from ai.memory import TimeDecayRanker
        ranker = TimeDecayRanker(similarity_floor=0.0)
        docs = ["a", "b"]
        metas = [
            {"timestamp": "2026-06-01T00:00:00", "importance": "high"},  # ISO 字符串 + 非数字
            {},                                                          # 全缺失
        ]
        dists = [0.1, 0.2]
        results = ranker.rank(docs, metas, dists, top_k=2, query="")  # 不应抛异常
        self.assertEqual(len(results), 2)

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_resolve_batch_uses_single_query(self, mock_get_col, mock_call_once):
        """#2: 多条新事实只做一次批量 col.query，而非逐条串行。"""
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_col.query.return_value = {  # 两条 query 各一个近邻冲突候选
            "documents": [["旧事实A"], ["旧事实B"]],
            "ids": [["10_0"], ["20_0"]],
            "distances": [[0.1], [0.2]],
        }
        mock_call_once.return_value = {"type": "text", "content": '["10_0", "20_0"]'}

        del_ids = await memory.ConflictResolver.resolve_batch(
            ["新事实1", "新事实2"], bot_id=5, group_id=9, provider="claude", model="x"
        )
        mock_col.query.assert_called_once()  # 单次批量，不再 N 次
        self.assertEqual(mock_col.query.call_args[1]["query_texts"], ["新事实1", "新事实2"])
        self.assertIn("10_0", del_ids)
        self.assertIn("20_0", del_ids)

    @patch("ai.client.call_ai_once", new_callable=AsyncMock)
    async def test_extract_keeps_short_config_facts(self, mock_call_once):
        """#3: 极短但关键的配置/技术决策（如 使用React19）不应被长度门静默丢弃。"""
        mock_call_once.return_value = {"type": "text", "content": "使用React19|0.9"}
        facts = await memory.FactExtractor.extract("使用React19", provider="claude", model="x")
        mock_call_once.assert_called_once()  # 未被长度门挡掉（旧阈值 15 会直接 return）
        self.assertEqual(facts, [("使用React19", 0.9)])

    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    async def test_maybe_reflect_skips_when_already_in_flight(self, mock_wm_get):
        """同 (bot,group) 已有反思在跑时，重入应立即返回、不做任何工作（防跨轮并发重复反思）。"""
        memory._reflect_in_flight.add((5, 9))
        try:
            await memory.maybe_reflect(group_id=9, bot_id=5, role="dev")
            mock_wm_get.assert_not_awaited()  # 短路：连水位线都没读
        finally:
            memory._reflect_in_flight.discard((5, 9))

    @patch("ai.memory._set_reflection_watermark", new_callable=AsyncMock)
    @patch("ai.memory._get_all_reflection_watermarks", new_callable=AsyncMock)
    @patch("ai.memory._get_collection")
    async def test_maybe_reflect_clears_in_flight_after_run(self, mock_get_col, mock_wm_get, mock_wm_set):
        """正常跑完后必须从 in-flight 集合移除，否则该 (bot,group) 再不会反思。"""
        mock_wm_get.return_value = {}
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        await memory.maybe_reflect(group_id=9, bot_id=5, role="dev")
        self.assertNotIn((5, 9), memory._reflect_in_flight)  # finally 已清理


class TestMemoryTopicScoping(unittest.IsolatedAsyncioTestCase):
    """讨论 topic（人给的议题）作为 summary 召回的作用域键：
    - 自由聊天（无活跃 thread）不强制注入历史摘要；
    - 讨论中只注入当前 topic 的摘要，不串入其它议题（如股票）。"""

    async def asyncSetUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
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

    @patch("ai.memory.retrieve_relevant", new_callable=AsyncMock)
    async def test_free_chat_injects_empty_thread_summaries_excludes_legacy_null(self, mock_retrieve):
        """自由聊天（thread_id=None）注入 thread_id='' 的自由聊天摘要；不注入讨论摘要，
        也不注入遗留未作用域（NULL）摘要——"无 topic" 统一用 ''，杜绝旧话题摘要串入自由聊天。"""
        mock_retrieve.return_value = []
        async with database.get_db() as db:
            await db.execute(
                "INSERT INTO role_summaries (bot_id, group_id, role, summary, covered_through_id, thread_id) "
                "VALUES (2, 1, 'Summarizer', 'STOCK_SUMMARY', 10, 'disc:stock')"
            )
            await db.execute(
                "INSERT INTO role_summaries (bot_id, group_id, role, summary, covered_through_id, thread_id) "
                "VALUES (2, 1, 'Summarizer', 'FREE_CHAT_SUMMARY', 11, '')"
            )
            await db.execute(
                "INSERT INTO role_summaries (bot_id, group_id, role, summary, covered_through_id, thread_id) "
                "VALUES (2, 1, 'Summarizer', 'LEGACY_SUMMARY', 12, NULL)"
            )
            await db.commit()

        result = await memory.get_memory_context(
            bot_id=2, role="Summarizer", query="任意自由聊天查询", group_id=1, thread_id=None
        )
        self.assertIn("我的历史经验摘要", result)
        self.assertIn("FREE_CHAT_SUMMARY", result)
        self.assertNotIn("STOCK_SUMMARY", result)
        self.assertNotIn("LEGACY_SUMMARY", result)  # 遗留 NULL 不再串入自由聊天

    @patch("ai.memory.retrieve_relevant", new_callable=AsyncMock)
    async def test_summary_injection_scoped_to_active_thread(self, mock_retrieve):
        """讨论中只注入当前 topic 的摘要，其它 topic（股票）的摘要不串入。"""
        mock_retrieve.return_value = []
        async with database.get_db() as db:
            await db.execute(
                "INSERT INTO role_summaries (bot_id, group_id, role, summary, covered_through_id, thread_id) "
                "VALUES (2, 1, 'Summarizer', 'MANGO_SUMMARY', 20, 'disc:mango')"
            )
            await db.execute(
                "INSERT INTO role_summaries (bot_id, group_id, role, summary, covered_through_id, thread_id) "
                "VALUES (2, 1, 'Summarizer', 'STOCK_SUMMARY', 10, 'disc:stock')"
            )
            await db.commit()

        result = await memory.get_memory_context(
            bot_id=2, role="Summarizer", query="吃芒果坏肚子", group_id=1, thread_id="disc:mango"
        )
        self.assertIn("MANGO_SUMMARY", result)
        self.assertNotIn("STOCK_SUMMARY", result)

    @patch("ai.client.call_ai", new=mock_call_ai)
    async def test_maybe_summarize_stamps_active_thread_id(self):
        """讨论中产出的摘要要打上当前 thread_id，供按 topic 召回。"""
        async with database.get_db() as db:
            for i in range(1, 17):
                await db.execute(
                    "INSERT INTO messages (id, group_id, member_id, content) VALUES (?, 1, 2, ?)",
                    (i, f"Message content {i}")
                )
            await db.commit()

        await memory.maybe_summarize(
            group_id=1, bot_id=2, role="Summarizer", member_ids=[2], thread_id="disc:mango"
        )

        async with database.get_db() as db:
            database_row = database.aiosqlite.Row
            db.row_factory = database_row
            async with db.execute("SELECT thread_id FROM role_summaries WHERE bot_id = 2") as cur:
                rows = await cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["thread_id"], "disc:mango")

    @patch("ai.client.call_ai", new=mock_call_ai)
    async def test_maybe_summarize_free_chat_stamps_empty_thread(self):
        """自由聊天（thread_id=None）产出的摘要落库为 ''（与事实/反思的空 thread 表示统一，
        不与遗留 NULL 行混淆）。"""
        async with database.get_db() as db:
            for i in range(1, 17):
                await db.execute(
                    "INSERT INTO messages (id, group_id, member_id, content) VALUES (?, 1, 2, ?)",
                    (i, f"Message content {i}")
                )
            await db.commit()

        await memory.maybe_summarize(
            group_id=1, bot_id=2, role="Summarizer", member_ids=[2], thread_id=None
        )

        async with database.get_db() as db:
            db.row_factory = database.aiosqlite.Row
            async with db.execute("SELECT thread_id FROM role_summaries WHERE bot_id = 2") as cur:
                rows = await cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["thread_id"], "")


if __name__ == "__main__":
    unittest.main()
