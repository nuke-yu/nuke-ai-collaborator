
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestration.rd_manager import rd_manager
import db

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DB = str(os.path.join(_HERE, "test_rd_manager_v3.db"))

class TestRDManagerV3(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"
        await db.init_db()

    async def asyncTearDown(self):
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_render_board_from_db(self):
        group_id = 1
        
        # 1. Insert dummy tickets into DB
        async with db.connect() as conn:
            await conn.execute(
                "INSERT INTO tickets (group_id, ticket_id, title, status, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (group_id, "JIRA-100", "Task 1", "backlog")
            )
            await conn.execute(
                "INSERT INTO tickets (group_id, ticket_id, title, status, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (group_id, "JIRA-101", "Task 2", "in_progress")
            )
            await conn.commit()

        written_content = ""
        async def mock_write(bot_id, path, text, group_id=None):
            nonlocal written_content
            written_content = text
            return "ok"

        with patch("core.orchestration.rd_manager.write_file", side_effect=mock_write):
            await rd_manager.render_board(group_id)

            self.assertIn("JIRA-100", written_content)
            self.assertIn("JIRA-101", written_content)
            self.assertIn("Backlog", written_content)
            self.assertIn("In Progress", written_content)

if __name__ == "__main__":
    unittest.main()
