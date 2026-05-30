import os
import sys
import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
from bus.events import TicketCreated
from core.orchestrator import init_event_handlers

_HERE = Path(__file__).parent.parent if 'Path' in locals() else os.path.dirname(__file__)
_TEST_DB = str(os.path.join(os.path.dirname(_HERE), "test_dispatch.db"))

class TestEventDispatch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _db_mod.DB_PATH = _TEST_DB
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
            # Seed a group and a Dev bot
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'Project Alpha')")
            await db.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (10, 1, 'DevBot', 'bot', 'dev')")
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_ticket_created_triggers_dispatch(self):
        from bus import bus
        
        # We need to capture the call to dispatch_bots
        with patch("core.orchestrator.dispatch_bots", new=AsyncMock(return_value=None)) as mock_dispatch, \
             patch("core.orchestrator.get_messages", new=AsyncMock(return_value=[])):
            
            # Initialize handlers
            await init_event_handlers()
            # Wait for background task to subscribe
            await asyncio.sleep(0.05)
            
            # Fire event
            ev = TicketCreated(group_id=1, ticket_id="JIRA-101", title="Fix Bug", description="...")
            await bus.publish(ev)
            
            # Give it time to process
            await asyncio.sleep(0.1)
            
            # Verify dispatch was called for our DevBot (id 10)
            mock_dispatch.assert_called_once()
            args = mock_dispatch.call_args[0]
            # args: (group_id, triggered_bots, content, sender, recent, ...)
            self.assertEqual(args[0], 1)
            self.assertEqual(args[1][0]["id"], 10)
            self.assertIn("JIRA-101", args[2])

if __name__ == "__main__":
    unittest.main()
