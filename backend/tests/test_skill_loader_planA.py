"""Plan A — loader enhancements: inline framing, companion cap, SKILL_DIR norm."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(coro):
    return asyncio.run(coro)


class TestInlineFraming(unittest.IsolatedAsyncioTestCase):
    async def test_inline_body_wrapped_in_skill_instructions(self):
        from unittest.mock import patch
        from skills import loader

        skill_dir = Path("/tmp/nuke_skill_x")
        entry = {
            "name": "demo", "type": "md", "path": skill_dir / "SKILL.md",
            "description": "d", "context": "inline",
        }

        async def fake_list(*a, **k):
            return [entry]

        with patch.object(loader, "available_skills_for_bot", new=fake_list), \
             patch("pathlib.Path.exists", lambda self: True), \
             patch("pathlib.Path.read_text", lambda self, encoding="utf-8": "BODY-TEXT"), \
             patch("pathlib.Path.iterdir", lambda self: iter([])):
            out = await loader.run_skill(1, "demo", "", ctx={"group_id": 1})

        self.assertTrue(out.startswith("<skill_instructions>"))
        self.assertIn("BODY-TEXT", out)
        self.assertTrue(out.rstrip().endswith("</skill_instructions>"))


class TestCompanionCap(unittest.IsolatedAsyncioTestCase):
    async def test_companion_listing_capped(self):
        from unittest.mock import patch
        from skills import loader

        skill_dir = Path("/tmp/nuke_skill_y")
        companions = [skill_dir / f"f{i}.py" for i in range(25)]
        entry = {"name": "big", "type": "md", "path": skill_dir / "SKILL.md",
                 "description": "d", "context": "inline"}

        async def fake_list(*a, **k):
            return [entry]

        def fake_iterdir(self):
            return iter(companions)

        with patch.object(loader, "available_skills_for_bot", new=fake_list), \
             patch("pathlib.Path.exists", lambda self: True), \
             patch("pathlib.Path.read_text", lambda self, encoding="utf-8": "BODY"), \
             patch("pathlib.Path.iterdir", new=fake_iterdir):
            out = await loader.run_skill(1, "big", "", ctx={"group_id": 1})

        # Only 10 listed; an overflow note for the remaining 15.
        self.assertEqual(out.count("/tmp/nuke_skill_y/f"), 10)
        self.assertIn("还有 15 个文件", out)


class TestSkillDirNormalization(unittest.IsolatedAsyncioTestCase):
    async def test_skill_dir_backslashes_normalized(self):
        from skills.processor import process_skill_content
        out = await process_skill_content(
            "see ${SKILL_DIR}/scripts/run.ps1",
            "C:\\workspaces\\group_1\\skills\\demo",
        )
        self.assertEqual(out, "see C:/workspaces/group_1/skills/demo/scripts/run.ps1")
        self.assertNotIn("\\", out)


if __name__ == "__main__":
    unittest.main()
