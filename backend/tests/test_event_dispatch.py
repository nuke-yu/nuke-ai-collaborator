import os
import sys
import unittest
import dataclasses
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
from bus.events import TicketCreated
from core.orchestration.plugins.rd_automation import _on_ticket_created

_HERE = os.path.dirname(os.path.abspath(__file__))
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
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role) "
                "VALUES (10, 1, 'DevBot', 'bot', 'dev')")
            await db.commit()

    async def asyncTearDown(self):
        _db_mod.DB_PATH = self._orig
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_ticket_created_dispatches_matched_dev_bot(self):
        # rd_automation._on_ticket_created selects a dev bot for the ticket and
        # hands an OrchestratorStep to wf.apply via bg.spawn_group. Patch both so
        # we capture the dispatched step without actually running the workflow.
        m = "core.orchestration.plugins.rd_automation."
        with patch(m + "wf.apply") as mock_apply, patch(m + "bg.spawn_group"):
            ev = TicketCreated(group_id=1, ticket_id="JIRA-101",
                               title="Fix Bug", description="...")
            await _on_ticket_created(dataclasses.asdict(ev))

        mock_apply.assert_called_once()
        group_id, step = mock_apply.call_args[0]
        self.assertEqual(group_id, 1)
        units = step.next_units
        self.assertEqual(len(units), 1)
        # Dispatched to our DevBot (id 10) with the ticket id in the trigger message
        self.assertEqual(units[0].bot["id"], 10)
        self.assertIn("JIRA-101", units[0].trigger_msg)


if __name__ == "__main__":
    unittest.main()
