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

    @patch("workspace.layout.external_global_skills_dir")
    def test_safe_path_resolves_allowed_roots_but_write_blocks_them(self, mock_system_skills):
        from workspace import _safe_path, write_file, read_file
        import asyncio
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            ws_path = Path(d1)
            # Create a mock group workspace structure to extract group_id 2
            group_ws_path = ws_path / "group_2" / "bots" / "bot_1"
            group_ws_path.mkdir(parents=True, exist_ok=True)
            
            system_skills_path = Path(d2)
            mock_system_skills.return_value = system_skills_path
            
            # create file inside system skills
            skill_file = system_skills_path / "global-skill.md"
            skill_file.write_text("global content")
            
            # create symlink in group_ws_path pointing to system_skills_path
            skills_dir = group_ws_path / "skills"
            skills_dir.mkdir()
            system_link = skills_dir / "system"
            system_link.symlink_to(system_skills_path, target_is_directory=True)
            
            # _safe_path should resolve it successfully
            resolved = _safe_path(group_ws_path, "skills/system/global-skill.md")
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.resolve(), skill_file.resolve())
            
            # read_file should succeed
            # We mock bot_workspace to return group_ws_path
            with patch("workspace.bot_workspace", return_value=group_ws_path):
                content = asyncio.run(read_file(bot_id=1, path="skills/system/global-skill.md", group_id=2))
                self.assertEqual(content, "global content")
                
                # write_file should fail with [只读]
                write_res = asyncio.run(write_file(bot_id=1, path="skills/system/global-skill.md", content="new content", group_id=2))
                self.assertIn("[只读]", write_res)
                
            # content should remain unchanged
            self.assertEqual(skill_file.read_text(), "global content")

if __name__ == "__main__":
    unittest.main()
