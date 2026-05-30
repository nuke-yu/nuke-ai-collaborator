import os
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
from core.orchestration.rd_manager import rd_manager
from bus.events import TicketCreated

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_rd_manager_v2.db")

class TestRDManagerV2(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _db_mod.DB_PATH = _TEST_DB
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        
        # Seed parents
        async with aiosqlite.connect(_TEST_DB) as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'g')")
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_archiving_logic(self):
        group_id = 1
        content = """
# Board
## Backlog
| JIRA-1 | Task 1 | Medium |
## Done
| JIRA-2 | Completed Task | High | Done |
"""
        # Mock board file
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = content
        
        # Capture the archived board content
        written_content = ""
        async def mock_write(bot_id, path, text):
            nonlocal written_content
            written_content = text
            return "ok"

        with patch("core.orchestration.rd_manager.group_workspace", return_value=MagicMock(__truediv__=lambda s, x: mock_path)), \
             patch("core.orchestration.rd_manager.write_file", side_effect=mock_write), \
             patch("bus.bus.publish", new=AsyncMock()):
            
            await rd_manager.check_board(group_id)
            
            # 1. JIRA-2 should be removed from board
            self.assertIn("JIRA-1", written_content)
            self.assertNotIn("JIRA-2", written_content)
            
            # 2. JIRA-2 should be in DB
            from db import connect
            async with connect() as db:
                async with db.execute("SELECT ticket_id, status FROM tickets WHERE ticket_id='JIRA-2'") as cur:
                    row = await cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[1], "done")

    async def test_status_sync_to_db(self):
        group_id = 1
        content = """
## In Progress
| JIRA-100 | Working | Med |
"""
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = content

        with patch("core.orchestration.rd_manager.group_workspace", return_value=MagicMock(__truediv__=lambda s, x: mock_path)), \
             patch("bus.bus.publish", new=AsyncMock()):
            
            await rd_manager.check_board(group_id)
            
            from db import connect
            async with connect() as db:
                async with db.execute("SELECT status FROM tickets WHERE ticket_id='JIRA-100'") as cur:
                    row = await cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "in_progress")

if __name__ == "__main__":
    unittest.main()
