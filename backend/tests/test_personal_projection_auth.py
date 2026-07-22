"""Unit tests verifying user group membership enforcement in personal projection creation."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.personal_memory import create_personal_projection


class TestPersonalProjectionAuth(unittest.IsolatedAsyncioTestCase):
    async def test_create_projection_denies_non_group_member(self) -> None:
        user = {"uid": 999, "sub": "test_user"}
        body = {"group_id": 1, "record_id": "rec:1"}

        mock_db_ctx = MagicMock()
        mock_db = MagicMock()
        mock_execute_ctx = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = None  # User 999 is NOT a member of group 1

        mock_execute_ctx.__aenter__.return_value = mock_cursor
        mock_db.execute.return_value = mock_execute_ctx
        mock_db_ctx.__aenter__.return_value = mock_db

        with patch("api.personal_memory.global_db", return_value=mock_db_ctx):
            with self.assertRaises(HTTPException) as cm:
                await create_personal_projection(body=body, user=user)
            self.assertEqual(cm.exception.status_code, 403)
            self.assertIn("not a member", cm.exception.detail)

    async def test_create_projection_allows_legitimate_human_member(self) -> None:
        user = {"uid": 10, "sub": "test_user"}
        body = {"group_id": 1, "record_id": "rec:1"}

        mock_db_ctx = MagicMock()
        mock_db = MagicMock()
        mock_execute_ctx = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = (1,)  # User 10 IS a member (type='human')

        mock_execute_ctx.__aenter__.return_value = mock_cursor
        mock_db.execute.return_value = mock_execute_ctx
        mock_db_ctx.__aenter__.return_value = mock_db

        mock_client = AsyncMock()
        mock_client.create_projection.return_value = "proj:100"

        with patch("api.personal_memory.global_db", return_value=mock_db_ctx), \
             patch("api.personal_memory.build_personal_knowledge_client", return_value=mock_client):
            res = await create_personal_projection(body=body, user=user)
            self.assertEqual(res, {"projection_id": "proj:100"})

            sql_call = mock_db.execute.call_args_list[0][0][0]
            self.assertIn("type='human'", sql_call)


if __name__ == "__main__":
    unittest.main()
