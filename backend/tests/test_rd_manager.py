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
        tickets = rd_manager._parse_backlog(content)
        self.assertEqual(len(tickets), 2)
        self.assertIn("JIRA-1", tickets)
        self.assertEqual(tickets["JIRA-1"]["title"], "Task 1")
        self.assertEqual(tickets["JIRA-2"]["priority"], "Low")
        self.assertNotIn("JIRA-3", tickets) # Should not be in backlog

    async def test_detect_new_ticket(self):
        group_id = 888
        rd_manager._last_tickets[group_id] = {"JIRA-1"}
        
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
             patch("bus.bus.publish", new=AsyncMock()) as mock_publish:
            
            await rd_manager.check_board(group_id)
            
            # Should have published one TicketCreated event for JIRA-2
            mock_publish.assert_awaited_once()
            ev = mock_publish.call_args[0][0]
            self.assertIsInstance(ev, TicketCreated)
            self.assertEqual(ev.ticket_id, "JIRA-2")
            self.assertEqual(ev.priority, "High")
            
            # Cache should be updated
            self.assertEqual(rd_manager._last_tickets[group_id], {"JIRA-1", "JIRA-2"})

if __name__ == "__main__":
    unittest.main()
