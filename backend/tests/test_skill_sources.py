# backend/tests/test_skill_sources.py
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from skills.sources.base import ScanCtx


class TestSystemSource(unittest.TestCase):
    def test_enumerate_lists_system_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            sysdir = Path(tmp) / "system" / "skills"
            sysdir.mkdir(parents=True)
            (sysdir / "read-file.md").write_text(
                "---\nname: read-file\ndescription: reads\n---\nbody", encoding="utf-8")
            with patch("skills.constants.SYSTEM_SKILLS_ROOT", sysdir), \
                 patch("skills.sources.system.SYSTEM_SKILLS_ROOT", sysdir):
                from skills.sources.system import SystemPoolSource
                src = SystemPoolSource(ScanCtx(bot_id=1))
                names = [s["name"] for s in src.enumerate()]
                self.assertIn("read-file", names)
                self.assertTrue(any("read-file.md" in str(p) for p in src.signature()))


class TestGroupSource(unittest.TestCase):
    def test_enumerate_group_skills_under_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("skills.constants.WORKSPACE_ROOT", Path(tmp)):
                from workspace import layout
                gdir = layout.group_shared_dir(3) / "skills"
                gdir.mkdir(parents=True)
                (gdir / "house-style.md").write_text(
                    "---\nname: house-style\ndescription: x\n---\nb", encoding="utf-8")
                from skills.sources.group import GroupSource
                from skills.sources.base import ScanCtx
                src = GroupSource(ScanCtx(bot_id=1, group_id=3))
                self.assertEqual([s["name"] for s in src.enumerate()], ["house-style"])
                self.assertEqual(src.enumerate()[0]["layer"], "group")

    def test_no_group_id_is_empty(self):
        from skills.sources.group import GroupSource
        from skills.sources.base import ScanCtx
        src = GroupSource(ScanCtx(bot_id=1, group_id=None))
        self.assertEqual(src.enumerate(), [])
        self.assertEqual(src.signature(), ())


class TestRoleSource(unittest.TestCase):
    def test_enumerate_role_skills_global_for_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            rdir = Path(tmp) / "roles" / "dev" / "skills"
            rdir.mkdir(parents=True)
            (rdir / "code-review.md").write_text(
                "---\nname: code-review\ndescription: x\n---\nb", encoding="utf-8")
            with patch("skills.sources.role.ROLES_ROOT", Path(tmp) / "roles"):
                from skills.sources.role import RoleSource
                from skills.sources.base import ScanCtx
                src = RoleSource(ScanCtx(bot_id=1, group_id=3, role="dev"))
                self.assertEqual([s["name"] for s in src.enumerate()], ["code-review"])

    def test_no_role_is_empty(self):
        from skills.sources.role import RoleSource
        from skills.sources.base import ScanCtx
        src = RoleSource(ScanCtx(bot_id=1, group_id=3, role=None))
        self.assertEqual(src.enumerate(), [])


if __name__ == "__main__":
    unittest.main()
