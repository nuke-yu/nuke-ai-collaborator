"""Tests for workspace.make_dir — the "new folder" primitive behind the
workspace panel's manual-skill creation (build a directory-form skill
folder-first, then add SKILL.md + scripts). Sandbox-confined to the bot ws.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace import make_dir, delete_path


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


class TestDeletePath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self._patch = patch("workspace.bot_workspace", return_value=self.ws)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_deletes_file(self):
        f = self.ws / "skills" / "tool.md"
        f.parent.mkdir(parents=True)
        f.write_text("x", encoding="utf-8")
        result = delete_path(7, "skills/tool.md")
        self.assertIn("已删除", result)
        self.assertFalse(f.exists())

    def test_deletes_dir_recursively(self):
        d = self.ws / "skills" / "mytool"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x", encoding="utf-8")
        (d / "run.sh").write_text("y", encoding="utf-8")
        result = delete_path(7, "skills/mytool")
        self.assertIn("已删除", result)
        self.assertFalse(d.exists())

    def test_rejects_traversal(self):
        result = delete_path(7, "../escape")
        self.assertTrue(result.startswith("[错误]"), result)

    def test_rejects_root(self):
        result = delete_path(7, "")
        self.assertTrue(result.startswith("[错误]"), result)
        self.assertTrue(self.ws.exists())

    def test_protects_memory_file(self):
        (self.ws / "MEMORY.md").write_text("keep", encoding="utf-8")
        result = delete_path(7, "MEMORY.md")
        self.assertTrue(result.startswith("[受保护]"), result)
        self.assertTrue((self.ws / "MEMORY.md").exists())

    def test_missing_file(self):
        result = delete_path(7, "skills/nope.md")
        self.assertTrue(result.startswith("[文件不存在]"), result)


if __name__ == "__main__":
    unittest.main()
