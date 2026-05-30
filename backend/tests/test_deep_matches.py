import unittest
import asyncio
from permissions.models import Rule, Ruleset
from permissions.engine import _matches

class TestDeepMatches(unittest.TestCase):
    def test_recursive_match_in_dict(self):
        rule = Rule(tool_pattern="run_shell", args_pattern="rm *", action="deny")
        
        # Top-level match (already worked)
        self.assertTrue(_matches(rule, "run_shell", {"cmd": "rm -rf /"}))
        
        # Nested match (the fix for DFT-044)
        self.assertTrue(_matches(rule, "run_shell", {"wrapper": {"cmd": "rm -rf /"}}))
        
    def test_recursive_match_in_list(self):
        rule = Rule(tool_pattern="multi_run", args_pattern="danger*", action="deny")
        
        # Match inside a list
        self.assertTrue(_matches(rule, "multi_run", {"cmds": ["ls", "danger_command"]}))
        
    def test_recursive_no_false_positive(self):
        rule = Rule(tool_pattern="run_shell", args_pattern="rm *", action="deny")
        
        # Should NOT match safe commands
        self.assertFalse(_matches(rule, "run_shell", {"wrapper": {"cmd": "ls -l"}}))
        self.assertFalse(_matches(rule, "run_shell", {"list": ["echo", "cat file"]}))

if __name__ == "__main__":
    unittest.main()
