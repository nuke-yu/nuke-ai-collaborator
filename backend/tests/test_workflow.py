import unittest
from unittest.mock import AsyncMock, patch
import sys
import os
import asyncio

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database

# Use a test database to isolate tests
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_chat.db")
database.DB_PATH = TEST_DB_PATH

# Mock call_ai_stream from ai_client to yield dummy tokens
async def mock_call_ai_stream(*args, **kwargs):
    yield "Part 1"
    yield "Part 2"

class TestWorkflowBroadcast(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Clean up database file if exists
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
        
        # Insert a test group and bot
        async with database.get_db() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role, avatar_color) VALUES (2, 1, 'WorkflowBot', 'bot', 'Developer', '#123456')"
            )
            await db.commit()

    async def asyncTearDown(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass
        import core.workflow as workflow
        workflow._state.clear()

    @patch("ai.client.call_ai_stream", new=mock_call_ai_stream)
    async def test_trigger_single_stage_broadcast_format(self):
        """Verify that _trigger_single_stage publishes typed StreamChunk events with a 'delta' field."""
        import core.workflow as workflow
        from core.workflow import _trigger_single_stage

        captured = []
        async def mock_publish(event):
            captured.append(event)

        with patch.object(workflow.bus, "publish", new=mock_publish):
            next_bot = {
                "id": 2,
                "name": "WorkflowBot",
                "role": "Developer",
                "avatar_color": "#123456",
                "system_prompt": "You are workflow bot",
                "personality_prompt": ""
            }
            await _trigger_single_stage(group_id=1, prev_stage={}, next_bot=next_bot)

            chunks = [e for e in captured if e.type == "stream_chunk"]
            self.assertTrue(len(chunks) > 0)
            for e in chunks:
                self.assertTrue(hasattr(e, "delta"))
                self.assertFalse(hasattr(e, "chunk"))
                self.assertIn(e.delta, ("Part 1", "Part 2"))

    @patch("ai.client.call_ai_stream", new=mock_call_ai_stream)
    async def test_trigger_pool_bot_broadcast_format(self):
        """Verify that _trigger_pool_bot publishes typed StreamChunk events with a 'delta' field."""
        import core.workflow as workflow
        from core.workflow import _trigger_pool_bot

        captured = []
        async def mock_publish(event):
            captured.append(event)

        bot = {
            "id": 2,
            "name": "WorkflowBot",
            "role": "Developer",
            "avatar_color": "#123456",
            "system_prompt": "You are workflow bot",
            "personality_prompt": ""
        }

        # Populate the workflow state so it doesn't return early
        workflow._state[1] = {
            "stages": [
                {"stage_type": "pool", "bots": [bot], "done_keyword": "完毕"}
            ],
            "current": 0
        }

        with patch.object(workflow.bus, "publish", new=mock_publish):
            await _trigger_pool_bot(group_id=1, bot=bot, ticket="Fix bug #123", pool_stage={"done_keyword": "完毕"})

            chunks = [e for e in captured if e.type == "stream_chunk"]
            self.assertTrue(len(chunks) > 0)
            for e in chunks:
                self.assertTrue(hasattr(e, "delta"))
                self.assertFalse(hasattr(e, "chunk"))
                self.assertIn(e.delta, ("Part 1", "Part 2"))


class TestWorkflowParseTickets(unittest.TestCase):
    def test_parse_tickets_numbered_header(self):
        from core.workflow import _parse_tickets
        msg = "Here are the tickets:\nTICKETS:\n1. Fix login bug\n2. Add settings page\nSome other text"
        self.assertEqual(_parse_tickets(msg), ["Fix login bug", "Add settings page"])

    def test_parse_tickets_bullet_header_hyphen(self):
        from core.workflow import _parse_tickets
        msg = "TICKETS:\n- Implement OAuth\n- Style layout"
        self.assertEqual(_parse_tickets(msg), ["Implement OAuth", "Style layout"])

    def test_parse_tickets_bullet_header_asterisk(self):
        from core.workflow import _parse_tickets
        msg = "TICKETS:\n* Setup CI/CD\n* Add unit tests"
        self.assertEqual(_parse_tickets(msg), ["Setup CI/CD", "Add unit tests"])

    def test_parse_tickets_no_header_numbered(self):
        from core.workflow import _parse_tickets
        msg = "We need to do:\n1. Write README\n2. Push to main"
        self.assertEqual(_parse_tickets(msg), ["Write README", "Push to main"])

    def test_parse_tickets_no_header_bullets(self):
        from core.workflow import _parse_tickets
        msg = "To-do list:\n- Create logo\n- Write backend code"
        self.assertEqual(_parse_tickets(msg), ["Create logo", "Write backend code"])

    def test_parse_tickets_fallback(self):
        from core.workflow import _parse_tickets
        msg = "No tickets list found here"
        self.assertEqual(_parse_tickets(msg), ["本次迭代任务"])

if __name__ == "__main__":
    unittest.main()

