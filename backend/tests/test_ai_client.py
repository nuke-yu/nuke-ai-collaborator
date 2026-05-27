import unittest
import json
import sys
import os

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_client import _to_claude_messages

class TestClaudeMessageFormatting(unittest.TestCase):

    def test_basic_conversion(self):
        """Test user and assistant message conversion."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"}
        ]
        expected = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"}
        ]
        self.assertEqual(_to_claude_messages(messages), expected)

    def test_consecutive_same_role_messages(self):
        """Test that consecutive messages with the same role are merged."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "world"},
            {"role": "assistant", "content": "how can I help?"},
            {"role": "assistant", "content": "today?"}
        ]
        # Merged result should have text blocks concatenated
        result = _to_claude_messages(messages)
        self.assertEqual(len(result), 2)
        
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(
            result[0]["content"],
            [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
        )
        
        self.assertEqual(result[1]["role"], "assistant")
        self.assertEqual(
            result[1]["content"],
            [{"type": "text", "text": "how can I help?"}, {"type": "text", "text": "today?"}]
        )

    def test_tool_use_and_tool_result_grouping(self):
        """Test tool calls and merging consecutive tool results."""
        messages = [
            {"role": "user", "content": "run the script"},
            {
                "role": "assistant",
                "content": "Sure, running it.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": '{"cmd": "echo 1"}'
                        }
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": '{"cmd": "echo 2"}'
                        }
                    }
                ]
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "output 1"
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "content": "output 2"
            }
        ]
        
        result = _to_claude_messages(messages)
        
        # We expect:
        # 1. user: "run the script"
        # 2. assistant: text "Sure, running it." + two "tool_use" blocks
        # 3. user: two "tool_result" blocks (grouped)
        self.assertEqual(len(result), 3)
        
        # Check user
        self.assertEqual(result[0], {"role": "user", "content": "run the script"})
        
        # Check assistant tool use
        self.assertEqual(result[1]["role"], "assistant")
        self.assertEqual(len(result[1]["content"]), 3)
        self.assertEqual(result[1]["content"][0], {"type": "text", "text": "Sure, running it."})
        self.assertEqual(result[1]["content"][1], {
            "type": "tool_use",
            "id": "call_1",
            "name": "run_shell",
            "input": {"cmd": "echo 1"}
        })
        self.assertEqual(result[1]["content"][2], {
            "type": "tool_use",
            "id": "call_2",
            "name": "run_shell",
            "input": {"cmd": "echo 2"}
        })
        
        # Check tool results grouped inside a single user message
        self.assertEqual(result[2]["role"], "user")
        self.assertEqual(len(result[2]["content"]), 2)
        self.assertEqual(result[2]["content"][0], {
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "output 1"
        })
        self.assertEqual(result[2]["content"][1], {
            "type": "tool_result",
            "tool_use_id": "call_2",
            "content": "output 2"
        })

    def test_strip_system_messages(self):
        """Test that system messages are removed since system prompt is passed separately in Claude API."""
        messages = [
            {"role": "system", "content": "you are a helper"},
            {"role": "user", "content": "hello"}
        ]
        expected = [
            {"role": "user", "content": "hello"}
        ]
        self.assertEqual(_to_claude_messages(messages), expected)

if __name__ == "__main__":
    unittest.main()
