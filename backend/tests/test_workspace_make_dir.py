"""Tests for workspace.make_dir — the "new folder" primitive behind the
workspace panel's manual-skill creation (build a directory-form skill
folder-first, then add SKILL.md + scripts). Sandbox-confined to the bot ws.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace import make_dir


class TestMakeDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        # private path → _get_effective_ws returns bot_workspace (no DB needed)
        self._patch = patch("workspace.bot_workspace", return_value=self.ws)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_creates_nested_dir(self):
        result = make_dir(7, "skills/mytool")
        self.assertIn("已创建目录", result)
        self.assertTrue((self.ws / "skills" / "mytool").is_dir())

    def test_rejects_traversal(self):
        result = make_dir(7, "../escape")
        self.assertTrue(result.startswith("[错误]"), result)
        self.assertFalse((self.ws.parent / "escape").exists())

    def test_existing_dir_is_idempotent(self):
        (self.ws / "skills").mkdir()
        result = make_dir(7, "skills")
        self.assertEqual(result, "目录已存在")

    def test_rejects_when_file_exists_at_path(self):
        (self.ws / "skills").mkdir()
        (self.ws / "skills" / "tool.md").write_text("x", encoding="utf-8")
        result = make_dir(7, "skills/tool.md")
        self.assertTrue(result.startswith("[错误]"), result)


if __name__ == "__main__":
    unittest.main()
