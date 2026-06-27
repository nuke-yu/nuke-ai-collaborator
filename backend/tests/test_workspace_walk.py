"""工作区目录遍历剪枝：rglob 急切枚举会卡在 node_modules/venv 等；walk_visible 用
os.walk 原地剪枝，绝不进入这些重型/隐藏目录，并对总条目数封顶。"""
import tempfile
import unittest
from pathlib import Path

from workspace import walk_visible, _WS_MAX_ENTRIES


class TestWalkVisible(unittest.TestCase):
    def test_prunes_heavy_and_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as d:
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

            paths, truncated = walk_visible(root)
            rels = {str(p.relative_to(root)).replace("\\", "/") for p in paths}

            self.assertIn("src", rels)
            self.assertIn("src/app.py", rels)
            self.assertIn("README.md", rels)
            self.assertFalse(truncated)
            for bad in ("node_modules", ".git", "__pycache__", "index.js", "config", "m.pyc"):
                self.assertFalse(any(bad in r for r in rels), f"{bad} 不应出现: {rels}")

    def test_skip_hidden_false_keeps_dotfiles_but_still_prunes_heavy(self):
        """UI 模式(skip_hidden=False)：保留 .gitignore 等 dotfile，但 node_modules/.git 仍剪。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".gitignore").write_text("x")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "a.js").write_text("y")
            (root / ".git").mkdir()
            (root / ".git" / "HEAD").write_text("z")

            paths, _ = walk_visible(root, skip_hidden=False)
            rels = {str(p.relative_to(root)).replace("\\", "/") for p in paths}

            self.assertIn(".gitignore", rels)                 # dotfile 文件保留
            # 用精确路径成员判断，避免 ".git" 子串误命中 ".gitignore"
            self.assertNotIn("node_modules", rels)            # 重型目录仍剪
            self.assertNotIn("node_modules/a.js", rels)
            self.assertNotIn(".git", rels)                    # .git 在忽略集，仍剪
            self.assertNotIn(".git/HEAD", rels)

    def test_truncates_at_cap(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for i in range(_WS_MAX_ENTRIES + 50):
                (root / f"f{i:04d}.txt").write_text("x")
            paths, truncated = walk_visible(root)
            self.assertTrue(truncated)
            self.assertLessEqual(len(paths), _WS_MAX_ENTRIES)


    def test_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            root = Path(d1)
            target = Path(d2)
            
            # Create a file in the target directory
            (target / "external-skill.md").write_text("content")
            
            # Create a symlink under root pointing to target
            link_dir = root / "skills"
            link_dir.mkdir()
            (link_dir / "system").symlink_to(target.resolve(), target_is_directory=True)
            
            paths, _ = walk_visible(root)
            rels = {str(p.relative_to(root)).replace("\\", "/") for p in paths}
            
            self.assertIn("skills", rels)
            self.assertIn("skills/system", rels)
            self.assertIn("skills/system/external-skill.md", rels)


if __name__ == "__main__":
    unittest.main()
