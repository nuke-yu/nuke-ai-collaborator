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


    def test_loader_role_without_group_returns_none(self):
        from skills.loader import _skills_dir_for_layer
        self.assertIsNone(_skills_dir_for_layer("role", bot_id=7, group_id=None, role="dev"))

    def test_l3_role_resolves_under_group_and_isolated(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from skills.sources.role import RoleSource
        from skills.sources.base import ScanCtx
        with tempfile.TemporaryDirectory() as tmp:
            with patch("skills.constants.WORKSPACE_ROOT", Path(tmp)):
                # layout resolves WORKSPACE_ROOT at call time, so import order vs. patch is irrelevant
                from workspace import layout
                rdir = layout.group_roles_dir(3) / "dev" / "skills"
                rdir.mkdir(parents=True)
                (rdir / "code-review.md").write_text(
                    "---\nname: code-review\ndescription: x\n---\nb", encoding="utf-8")
                # group 3 sees it
                src3 = RoleSource(ScanCtx(bot_id=7, group_id=3, role="dev"))
                self.assertEqual([s["name"] for s in src3.enumerate()], ["code-review"])
                # group 4 (no such dir) sees nothing — cross-group isolation
                src4 = RoleSource(ScanCtx(bot_id=7, group_id=4, role="dev"))
                self.assertEqual(src4.enumerate(), [])


if __name__ == "__main__":
    unittest.main()
