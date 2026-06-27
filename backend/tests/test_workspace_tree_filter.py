import unittest
from pathlib import Path
import tempfile
import os
from unittest.mock import patch, MagicMock

from workspace import list_workspace_tree

class TestWorkspaceTreeFilter(unittest.TestCase):
    @patch("workspace.bot_workspace")
    @patch("workspace.walk_visible")
    @patch("skills.discovery._list_skills_all_sync")
    def test_list_workspace_tree_filters_disabled_skills(self, mock_list_skills, mock_walk_visible, mock_bot_ws):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir)
            mock_bot_ws.return_value = ws_path
            
            # Create a structure on disk
            # skills/system/read-file.md
            # skills/system/create-skill.md
            system_dir = ws_path / "skills" / "system"
            system_dir.mkdir(parents=True)
            
            read_file_md = system_dir / "read-file.md"
            read_file_md.touch()
            
            create_skill_md = system_dir / "create-skill.md"
            create_skill_md.touch()
            
            # walk_visible returns these Paths
            mock_walk_visible.return_value = (
                [
                    ws_path / "skills",
                    ws_path / "skills" / "system",
                    read_file_md,
                    create_skill_md,
                ],
                False
            )
            
            # mock_list_skills returns create-skill as disabled, read-file as active
            mock_list_skills.return_value = [
                {"name": "create-skill", "status": "disabled"},
                {"name": "read-file", "status": "active"},
            ]
            
            # Call list_workspace_tree
            result = list_workspace_tree(bot_id=1, group_id=2, role="PM")
            
            # Check results
            paths = {r["path"] for r in result}
            self.assertIn("skills", paths)
            self.assertIn("skills/system", paths)
            self.assertIn("skills/system/read-file.md", paths)
            self.assertNotIn("skills/system/create-skill.md", paths)

if __name__ == "__main__":
    unittest.main()
