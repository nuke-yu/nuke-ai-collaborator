"""Phase 3: shell 沙箱在 bot 私有区之外，额外放行本群组共享区（Dev/QA 共享工作树）。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from executors.plugins.workspace_tools import _resolve_shell_cwd
from workspace import layout


class TestShellCwdShared(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch("skills.constants.WORKSPACE_ROOT", Path(self._tmp.name).resolve())
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_empty_cwd_defaults_to_shared(self):
        # 共享优先：空 cwd → 群组共享区根（`mkdir -p workspace/pacman` 不带 cwd 也落共享）
        path, err = _resolve_shell_cwd("", bot_id=7, group_id=3)
        self.assertEqual(err, "")
        self.assertEqual(path, layout.group_shared_dir(3).resolve())

    def test_bare_relative_cwd_defaults_to_shared(self):
        path, err = _resolve_shell_cwd("subdir", bot_id=7, group_id=3)
        self.assertEqual(err, "")
        self.assertEqual(path, (layout.group_shared_dir(3) / "subdir").resolve())

    def test_private_prefix_cwd_stays_private(self):
        for c in ("skills/learned", "logs"):
            with self.subTest(cwd=c):
                path, err = _resolve_shell_cwd(c, bot_id=7, group_id=3)
                self.assertEqual(err, "")
                self.assertEqual(path, (layout.bot_dir(3, 7) / c).resolve())

    def test_no_group_context_falls_back_to_private(self):
        path, err = _resolve_shell_cwd("", bot_id=7, group_id=None)
        self.assertEqual(err, "")
        self.assertEqual(path, layout.bot_dir(None, 7).resolve())

    def test_shared_prefix_resolves_into_group_shared(self):
        path, err = _resolve_shell_cwd("workspace/repo1", bot_id=7, group_id=3)
        self.assertEqual(err, "")
        self.assertEqual(path, (layout.group_shared_dir(3) / "workspace" / "repo1").resolve())

    def test_absolute_shared_path_allowed(self):
        target = layout.group_shared_dir(3) / "workspace" / "repo1"
        path, err = _resolve_shell_cwd(str(target), bot_id=7, group_id=3)
        self.assertEqual(err, "")
        self.assertEqual(path, target.resolve())

    def test_other_group_shared_rejected(self):
        other = layout.group_shared_dir(99) / "workspace"
        path, err = _resolve_shell_cwd(str(other), bot_id=7, group_id=3)
        self.assertIsNone(path)
        self.assertNotEqual(err, "")

    def test_traversal_escape_rejected(self):
        path, err = _resolve_shell_cwd("../../etc", bot_id=7, group_id=3)
        self.assertIsNone(path)
        self.assertNotEqual(err, "")


if __name__ == "__main__":
    unittest.main()
