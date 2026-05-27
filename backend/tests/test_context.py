import unittest
import sys
import os

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from role_router import build_context_message

class TestBuildContextMessage(unittest.TestCase):
    def test_build_context_no_duplication(self):
        """Verify that build_context_message deduplicates the latest message if it's already in recent_messages."""
        recent_messages = [
            {"sender_name": "Alice", "sender_type": "human", "content": "Hello", "created_at": "2026-05-26 12:00:00"},
            {"sender_name": "BobBot", "sender_type": "bot", "content": "Hi there!", "created_at": "2026-05-26 12:01:00"},
            {"sender_name": "Alice", "sender_type": "human", "content": "How are you?", "created_at": "2026-05-26 12:02:00"},
        ]
        
        # Call build_context_message with the latest message
        history, user_message = build_context_message(
            message="How are you?",
            sender_name="Alice",
            recent_messages=recent_messages
        )
        
        # The history should NOT contain the last message ("How are you?") to prevent duplication
        # when the user message gets appended later.
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["content"], "[Alice]: Hello")
        self.assertEqual(history[1]["content"], "[BobBot]: Hi there!")
        self.assertEqual(user_message, "[Alice]: How are you?")

    def test_build_context_with_different_last_message(self):
        """Verify that build_context_message does not filter anything if the latest message does not match."""
        recent_messages = [
            {"sender_name": "Alice", "sender_type": "human", "content": "Hello", "created_at": "2026-05-26 12:00:00"},
            {"sender_name": "BobBot", "sender_type": "bot", "content": "Hi there!", "created_at": "2026-05-26 12:01:00"},
        ]
        
        # Call build_context_message with a new message that hasn't been saved yet
        history, user_message = build_context_message(
            message="New question",
            sender_name="Alice",
            recent_messages=recent_messages
        )
        
        # History should include all recent messages
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["content"], "[Alice]: Hello")
        self.assertEqual(history[1]["content"], "[BobBot]: Hi there!")
        self.assertEqual(user_message, "[Alice]: New question")

if __name__ == "__main__":
    unittest.main()
