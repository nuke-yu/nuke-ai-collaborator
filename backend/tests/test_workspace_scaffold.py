"""Scaffolding lands physically in the right place (group-nested), and a new
group eagerly materialises the full shared coordination spec.

Regression for the group_3 bug: add_member passed no group_id, so init_bot_workspace
wrote a bot's IDENTITY/SOUL/MEMORY into the legacy flat workspaces/bot_{id} instead
of workspaces/group_{gid}/bots/bot_{id}.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import workspace


class TestBotScaffoldLanding(unittest.TestCase):
    def test_bot_files_land_in_group_nested_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                asyncio.run(workspace.init_bot_workspace({
                    "id": 7, "group_id": 3, "name": "Dev", "role": "dev",
                    "system_prompt": "sp", "personality_prompt": "pp",
                }))
                nested = root / "group_3" / "bots" / "bot_7"
                for f in ("IDENTITY.md", "SOUL.md", "BOOTSTRAP.md", "AGENT.md", "MEMORY.md"):
                    self.assertTrue((nested / f).exists(), f"{f} should be in the group-nested dir")
                # the 5 files must NOT have leaked into the legacy flat location
                self.assertFalse((root / "bot_7").exists(),
                                 "no files should land in the flat legacy path when group_id is known")


class TestGroupSharedSpec(unittest.TestCase):
    def test_init_group_creates_full_shared_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                asyncio.run(workspace.init_group_workspace(5, "Proj"))
                shared = root / "group_5" / "shared"
                # four fixed coordination files materialised at creation
                for f in ("BOARD.md", "SPEC.md", "API_CONTRACT.md", "RETRO_LATEST.md"):
                    self.assertTrue((shared / f).exists(), f"{f} should exist after group creation")
                # spec directories
                for d in ("docs", "workspace", "skills", "prs"):
                    self.assertTrue((shared / d).is_dir(), f"{d}/ should exist")
                # runs lives at the group level (sibling of shared)
                self.assertTrue((root / "group_5" / "runs").is_dir())


if __name__ == "__main__":
    unittest.main()
