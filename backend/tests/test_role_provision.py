# backend/tests/test_role_provision.py
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from workspace.role_provision import provision_group_roles
from workspace import layout


class TestProvision(unittest.TestCase):
    def _seed_template(self, root: Path, lang: str, role: str, skills: dict):
        tdir = root / "templates" / lang / "roles" / role
        (tdir / "skills").mkdir(parents=True)
        (tdir / "role.yaml").write_text(
            f"display_name: {role}\navatar_color: '#111'\n", encoding="utf-8")
        for name, body in skills.items():
            (tdir / "skills" / f"{name}.md").write_text(body, encoding="utf-8")

    def test_provisions_roles_into_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                self._seed_template(root, "zh", "系统架构师",
                                    {"design-architecture": "---\nname: design-architecture\n---\nb"})
                created = provision_group_roles(7, lang="zh")
                self.assertEqual(created, ["系统架构师"])
                rdir = layout.group_roles_dir(7) / "系统架构师"
                self.assertTrue((rdir / "role.yaml").exists())
                self.assertTrue((rdir / "skills" / "design-architecture.md").exists())

    def test_idempotent_second_call_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                self._seed_template(root, "zh", "PM", {"write-spec": "---\nname: write-spec\n---\nb"})
                self.assertEqual(provision_group_roles(7, lang="zh"), ["PM"])
                self.assertEqual(provision_group_roles(7, lang="zh"), [])  # already there

    def test_no_templates_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("skills.constants.WORKSPACE_ROOT", Path(tmp)):
                self.assertEqual(provision_group_roles(7, lang="en"), [])

    def test_init_group_workspace_provisions_roles(self):
        import asyncio
        from workspace import init_group_workspace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                self._seed_template(root, "zh", "代码助手", {"code-review": "---\nname: code-review\n---\nb"})
                asyncio.run(init_group_workspace(5, "Proj"))
                self.assertTrue(
                    (layout.group_roles_dir(5) / "代码助手" / "skills" / "code-review.md").exists())

    def test_init_group_workspace_logs_when_role_link_reset_fails(self):
        import asyncio
        from workspace import init_bot_workspace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                bot = {"id": 1, "group_id": 5, "name": "Bot", "role": "PM"}
                skills_dir = layout.bot_dir(5, 1) / "skills"
                skills_dir.mkdir(parents=True, exist_ok=True)
                bad_link = skills_dir / "role"
                bad_link.symlink_to("../../../shared/skills")

                orig_unlink = Path.unlink

                def fake_unlink(self, *args, **kwargs):
                    if self == bad_link:
                        raise OSError("unlink failed")
                    return orig_unlink(self, *args, **kwargs)

                with patch("pathlib.Path.unlink", new=fake_unlink), \
                     self.assertLogs("workspace", level="ERROR") as logs:
                    asyncio.run(init_bot_workspace(bot))

                self.assertTrue(any("failed to reset role skills link" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
