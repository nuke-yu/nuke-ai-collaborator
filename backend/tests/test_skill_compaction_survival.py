"""Plan A §7.5.3 — invoked inline skill bodies survive compaction via reinject."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.tool_loop_v1_helpers import build_invoked_skills_block


class TestInvokedSkillsBlock(unittest.TestCase):
    def test_empty_returns_blank(self):
        self.assertEqual(build_invoked_skills_block({}), "")

    def test_renders_active_skill_blocks(self):
        block = build_invoked_skills_block({"deploy": "<skill_instructions>STEP-A</skill_instructions>"})
        self.assertIn('<active_skill name="deploy">', block)
        self.assertIn("STEP-A", block)
        self.assertTrue(block.rstrip().endswith("</active_skill>"))

    def test_budget_truncates_oldest_first(self):
        inv = {f"s{i}": "X" * 5000 for i in range(5)}  # 25k of bodies
        block = build_invoked_skills_block(inv, budget=6000)
        # Only the most-recent entries fit the 6000-char budget.
        self.assertLessEqual(len(block), 6000 + 200)  # +small framing overhead
        self.assertIn('name="s4"', block)             # newest kept
        self.assertNotIn('name="s0"', block)          # oldest dropped


if __name__ == "__main__":
    unittest.main()
