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


if __name__ == "__main__":
    unittest.main()
