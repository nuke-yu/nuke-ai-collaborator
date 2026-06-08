"""Tests for #3: sub-agent permission attenuation (derive_subagent_ruleset).

Blast-radius containment for spawned agents — bypass doesn't propagate, blanket
high-risk allows are dropped; deny + scoped allows are kept.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permissions import derive_subagent_ruleset
from permissions.models import Rule, Ruleset


def _patterns(rs, action=None):
    return {(r.tool_pattern, r.args_pattern) for r in rs.rules
            if action is None or r.action == action}


class TestDeriveSubagentRuleset(unittest.TestCase):

    def test_none_passthrough(self):
        self.assertIsNone(derive_subagent_ruleset(None))

    def test_bypass_does_not_propagate(self):
        child = derive_subagent_ruleset(Ruleset(mode="bypassPermissions"))
        self.assertEqual(child.mode, "default")

    def test_default_mode_preserved(self):
        self.assertEqual(derive_subagent_ruleset(Ruleset(mode="default")).mode, "default")

    def test_dontask_mode_preserved(self):
        self.assertEqual(derive_subagent_ruleset(Ruleset(mode="dontAsk")).mode, "dontAsk")

    def test_blanket_high_risk_allow_dropped(self):
        parent = Ruleset(rules=[
            Rule(tool_pattern="run_shell", args_pattern="", action="allow"),
        ])
        child = derive_subagent_ruleset(parent)
        self.assertEqual(child.rules, [])   # blanket run_shell allow stripped

    def test_wildcard_blanket_allow_dropped(self):
        parent = Ruleset(rules=[Rule(tool_pattern="*", args_pattern="", action="allow")])
        self.assertEqual(derive_subagent_ruleset(parent).rules, [])

    def test_scoped_high_risk_allow_kept(self):
        parent = Ruleset(rules=[
            Rule(tool_pattern="run_shell", args_pattern="git push *", action="allow"),
        ])
        child = derive_subagent_ruleset(parent)
        self.assertIn(("run_shell", "git push *"), _patterns(child, "allow"))

    def test_deny_rules_always_kept(self):
        parent = Ruleset(rules=[
            Rule(tool_pattern="run_shell", args_pattern="", action="deny"),
            Rule(tool_pattern="*", args_pattern="", action="deny"),
        ])
        child = derive_subagent_ruleset(parent)
        self.assertEqual(len(child.rules), 2)
        self.assertTrue(all(r.action == "deny" for r in child.rules))

    def test_low_risk_blanket_allow_kept(self):
        # read_file is a low-risk workspace read — a blanket allow is fine to inherit.
        parent = Ruleset(rules=[Rule(tool_pattern="read_file", args_pattern="", action="allow")])
        child = derive_subagent_ruleset(parent)
        self.assertIn(("read_file", ""), _patterns(child, "allow"))

    def test_mixed_ruleset(self):
        parent = Ruleset(mode="bypassPermissions", rules=[
            Rule(tool_pattern="run_shell", args_pattern="", action="allow"),       # drop
            Rule(tool_pattern="run_shell", args_pattern="npm run *", action="allow"),  # keep
            Rule(tool_pattern="write_file", args_pattern="", action="deny"),       # keep (deny)
            Rule(tool_pattern="spawn_agent", args_pattern="", action="allow"),     # drop (high-risk blanket)
        ])
        child = derive_subagent_ruleset(parent)
        self.assertEqual(child.mode, "default")
        kept = _patterns(child)
        self.assertIn(("run_shell", "npm run *"), kept)
        self.assertIn(("write_file", ""), kept)
        self.assertNotIn(("run_shell", ""), kept)
        self.assertNotIn(("spawn_agent", ""), kept)

    def test_blanket_mcp_write_allow_dropped(self):
        # MCP rules look like mcp::server::tool — a blanket write allow must drop.
        parent = Ruleset(rules=[
            Rule(tool_pattern="mcp::filesystem::write_file", args_pattern="", action="allow"),
        ])
        self.assertEqual(derive_subagent_ruleset(parent).rules, [])

    def test_blanket_mcp_read_allow_kept(self):
        parent = Ruleset(rules=[
            Rule(tool_pattern="mcp::filesystem::read_file", args_pattern="", action="allow"),
        ])
        self.assertEqual(len(derive_subagent_ruleset(parent).rules), 1)  # read = not high-risk

    def test_mcp_wildcard_allow_dropped(self):
        parent = Ruleset(rules=[Rule(tool_pattern="mcp::*", args_pattern="", action="allow")])
        self.assertEqual(derive_subagent_ruleset(parent).rules, [])

    def test_scoped_mcp_write_allow_kept(self):
        # a non-blanket MCP allow (has args_pattern) is a real pre-approval → keep
        parent = Ruleset(rules=[
            Rule(tool_pattern="mcp::filesystem::write_file", args_pattern="/tmp/*", action="allow"),
        ])
        self.assertEqual(len(derive_subagent_ruleset(parent).rules), 1)

    def test_does_not_mutate_parent(self):
        parent = Ruleset(mode="bypassPermissions", rules=[
            Rule(tool_pattern="run_shell", args_pattern="", action="allow"),
        ])
        derive_subagent_ruleset(parent)
        self.assertEqual(parent.mode, "bypassPermissions")     # parent untouched
        self.assertEqual(len(parent.rules), 1)


if __name__ == "__main__":
    unittest.main()
