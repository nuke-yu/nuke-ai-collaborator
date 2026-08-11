"""Unit tests verifying fence token protection for pipeline job claims."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.runtime.learning_legacy import LegacyPipelineJobAdapter
from memory.domain import MemoryScope


class TestLearningLeaseFencing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = LegacyPipelineJobAdapter()
        self.scope = MemoryScope.group(group_id=1, actor_id="user:1")

    async def test_claim_returns_lease_token(self) -> None:
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("ai.memory._memory_db", return_value=mock_db_ctx):
            token = await self.adapter.claim(self.scope, "job:1")
            self.assertIsNotNone(token)
            self.assertTrue(token.startswith("fence:"))

    async def test_complete_checks_lease_token(self) -> None:
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("ai.memory._memory_db", return_value=mock_db_ctx):
            success = await self.adapter.complete(self.scope, "job:1", lease_token="fence:valid123")
            self.assertTrue(success)

            sql_call = mock_db.execute.call_args[0][0]
            self.assertIn("lease_token=?", sql_call)
            self.assertIn("status='running'", sql_call)

    async def test_stale_worker_with_mismatched_token_cannot_complete(self) -> None:
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0  # Stale token failed DB match

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("ai.memory._memory_db", return_value=mock_db_ctx):
            success = await self.adapter.complete(self.scope, "job:1", lease_token="fence:stale_token")
            self.assertFalse(success)

    async def test_fail_uses_persisted_retry_limit(self) -> None:
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_cursor)
        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("ai.memory._memory_db", return_value=mock_db_ctx):
            success = await self.adapter.fail(
                self.scope, "job:1", lease_token="fence:valid", error_message="failed"
            )

        self.assertTrue(success)
        sql, params = mock_db.execute.await_args.args
        self.assertIn("attempt>=max_attempts", sql)
        self.assertNotIn("attempt>=?", sql)
        self.assertEqual(params[0], "failed")

    async def test_pending_write_is_deterministic_and_upserted(self) -> None:
        mock_db = AsyncMock()
        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("ai.memory._memory_db", return_value=mock_db_ctx):
            first = await self.adapter.put_pending_write(
                self.scope, "chk:1", "task:1", "messages", {"ok": True}
            )
            second = await self.adapter.put_pending_write(
                self.scope, "chk:1", "task:1", "messages", {"ok": False}
            )

        self.assertEqual(first, second)
        self.assertIn("ON CONFLICT(group_id,checkpoint_id,task_id,channel)",
                      mock_db.execute.call_args_list[-1].args[0])
        self.assertEqual(mock_db.commit.await_count, 2)

    async def test_acknowledge_pending_writes_is_scoped_to_thread_checkpoint(self) -> None:
        cursor = MagicMock(rowcount=2)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=cursor)
        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("ai.memory._memory_db", return_value=mock_db_ctx):
            count = await self.adapter.acknowledge_pending_writes(self.scope, "chk:1")

        self.assertEqual(count, 2)
        sql, params = mock_db.execute.call_args_list[-1].args
        self.assertIn("DELETE FROM memory_checkpoint_pending_writes", sql)
        self.assertEqual(params, (1, "chk:1"))


if __name__ == "__main__":
    unittest.main()
