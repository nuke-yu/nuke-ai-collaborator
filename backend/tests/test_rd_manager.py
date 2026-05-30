import os
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestration.rd_manager import rd_manager
from bus.events import ToolResult, TicketCreated
from bus import bus

class TestRDManager(unittest.IsolatedAsyncioTestCase):
    async def test_parse_backlog(self):
        content = """
# Board
## Backlog
| # | Task | Priority |
|---|---|---|
| JIRA-1 | Task 1 | High |
| JIRA-2 | Task 2 | Low |

## In Progress
| JIRA-3 | Working | Med |
"""
        tickets = rd_manager._parse_board(content)
        self.assertEqual(len(tickets), 3)
        self.assertIn("JIRA-1", tickets)
        self.assertEqual(tickets["JIRA-1"]["title"], "Task 1")
        self.assertEqual(tickets["JIRA-2"]["status"], "backlog")
        self.assertIn("JIRA-3", tickets) 

    async def test_detect_new_ticket(self):
        group_id = 888
        rd_manager._last_tickets[group_id] = {"JIRA-1": "backlog"}
        
        # Mock file content
        content = """
## Backlog
| JIRA-1 | Old | Low |
| JIRA-2 | New Task | High |
"""
        
        # Patch group_workspace and board_path.read_text
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = content
        
        with patch("core.orchestration.rd_manager.group_workspace", return_value=MagicMock(__truediv__=lambda s, x: mock_path)), \
             patch("bus.bus.publish", new=AsyncMock()) as mock_publish, \
             patch("core.orchestration.rd_manager.write_file", new=AsyncMock()):
            
            await rd_manager.check_board(group_id)
            
            # Should have published one TicketCreated event for JIRA-2
            mock_publish.assert_awaited_once()
            ev = mock_publish.call_args[0][0]
            self.assertIsInstance(ev, TicketCreated)
            self.assertEqual(ev.ticket_id, "JIRA-2")
            
            # Cache should be updated
            self.assertEqual(rd_manager._last_tickets[group_id], {"JIRA-1": "backlog", "JIRA-2": "backlog"})

if __name__ == "__main__":
    unittest.main()
