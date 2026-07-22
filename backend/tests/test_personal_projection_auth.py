"""Unit tests verifying personal projection target validation."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.personal_memory import create_personal_projection


class TestPersonalProjectionAuth(unittest.IsolatedAsyncioTestCase):
    async def test_create_projection_rejects_nonexistent_group(self) -> None:
        user = {"uid": 999, "sub": "test_user"}
        body = {"group_id": 9999, "record_id": "rec:1"}

        mock_db_ctx = MagicMock()
        mock_db = MagicMock()
        mock_execute_ctx = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = None  # Group does not exist

        mock_execute_ctx.__aenter__.return_value = mock_cursor
        mock_db.execute.return_value = mock_execute_ctx
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("api.personal_memory.global_db", return_value=mock_db_ctx):
            with self.assertRaises(HTTPException) as cm:
                await create_personal_projection(body=body, user=user)
            self.assertEqual(cm.exception.status_code, 404)
            self.assertIn("target not found", cm.exception.detail)

    async def test_create_projection_allows_group_member_for_valid_group(self) -> None:
        user = {"uid": 10, "sub": "test_user"}
        body = {"group_id": 1, "record_id": "rec:1"}

        mock_db_ctx = MagicMock()
        mock_db = MagicMock()
        mock_execute_ctx = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = (1,)  # Group 1 exists

        mock_execute_ctx.__aenter__.return_value = mock_cursor
        mock_db.execute.return_value = mock_execute_ctx
        mock_db_ctx.__aenter__.return_value = mock_db

        mock_client = AsyncMock()
        mock_client.create_projection.return_value = "proj:100"

        with patch("api.personal_memory.global_db", return_value=mock_db_ctx), \
             patch("api.personal_memory.build_personal_knowledge_client", return_value=mock_client):
            res = await create_personal_projection(body=body, user=user)
            self.assertEqual(res, {"projection_id": "proj:100"})

        sql, params = mock_db.execute.call_args.args
        self.assertIn("group_memberships", sql)
        self.assertEqual(params, (10, 1))

    async def test_create_projection_rejects_authenticated_non_member(self) -> None:
        user = {"uid": 11, "sub": "outsider"}
        body = {"group_id": 1, "record_id": "rec:1"}
        mock_db_ctx = MagicMock()
        mock_db = MagicMock()
        mock_execute_ctx = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = None
        mock_execute_ctx.__aenter__.return_value = mock_cursor
        mock_db.execute.return_value = mock_execute_ctx
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("api.personal_memory.global_db", return_value=mock_db_ctx):
            with self.assertRaises(HTTPException) as cm:
                await create_personal_projection(body=body, user=user)
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
