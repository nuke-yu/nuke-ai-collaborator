"""工作区目录遍历剪枝：rglob 急切枚举会卡在 node_modules/venv 等；_walk_visible 用
os.walk 原地剪枝，绝不进入这些重型/隐藏目录，并对总条目数封顶。"""
import os
import tempfile
import unittest
from pathlib import Path

from workspace import _walk_visible, _WS_MAX_ENTRIES


class TestWalkVisible(unittest.TestCase):
    def test_prunes_heavy_and_hidden_dirs(self):
        d = tempfile.mkdtemp()
        root = Path(d)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("x")
        (root / "README.md").write_text("r")
        # 重型/隐藏目录：本身及其内容都不应出现
        (root / "node_modules" / "pkg").mkdir(parents=True)
        (root / "node_modules" / "pkg" / "index.js").write_text("y")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("z")
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "m.pyc").write_text("c")

        paths, truncated = _walk_visible(root)
        rels = {str(p.relative_to(root)).replace("\\", "/") for p in paths}

        self.assertIn("src", rels)
        self.assertIn("src/app.py", rels)
        self.assertIn("README.md", rels)
        self.assertFalse(truncated)
        # 被剪枝的目录连同内容全部缺席（且没有被急切枚举）
        for bad in ("node_modules", ".git", "__pycache__", "index.js", "config", "m.pyc"):
            self.assertFalse(any(bad in r for r in rels), f"{bad} 不应出现: {rels}")

    def test_truncates_at_cap(self):
        d = tempfile.mkdtemp()
        root = Path(d)
        for i in range(_WS_MAX_ENTRIES + 50):
            (root / f"f{i:04d}.txt").write_text("x")
        paths, truncated = _walk_visible(root)
        self.assertTrue(truncated)
        self.assertLessEqual(len(paths), _WS_MAX_ENTRIES)


if __name__ == "__main__":
    unittest.main()
