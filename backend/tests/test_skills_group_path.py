"""skills 层私有技能路径收归群组下：bots/bot_{id}/skills/。"""
import unittest

from workspace import layout


class TestSkillsGroupPath(unittest.TestCase):
    def test_loader_personal_dir_under_group(self):
        from skills.loader import _skills_dir_for_layer
        d = _skills_dir_for_layer("learned", bot_id=7, group_id=3, role=None)
        self.assertEqual(d, layout.bot_dir(3, 7) / "skills" / "learned" / "active")

    def test_loader_personal_root_under_group(self):
        from skills.loader import _skills_dir_for_layer
        d = _skills_dir_for_layer("personal", bot_id=7, group_id=3, role=None)
        self.assertEqual(d, layout.bot_dir(3, 7) / "skills")

    def test_discovery_scan_signature_uses_group(self):
        # _scan_signature 走 bot_ws(bot_id, group_id)：构造一个临时群组私有技能目录，
        # 断言指纹能感知该目录（即扫的是嵌套路径）。
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from skills import discovery
        with tempfile.TemporaryDirectory() as tmp:
            with patch("skills.constants.WORKSPACE_ROOT", Path(tmp)):
                sdir = layout.bot_dir(3, 7) / "skills"
                sdir.mkdir(parents=True)
                (sdir / "foo.md").write_text("x", encoding="utf-8")
                sig = discovery._scan_signature(7, 3, None)
                self.assertTrue(any("foo.md" in str(part) for part in sig))


if __name__ == "__main__":
    unittest.main()
