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


if __name__ == "__main__":
    unittest.main()
