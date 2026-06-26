# backend/tests/test_migrate_role_skills.py
import io, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import scripts.migrate_role_skills as M


class TestScaffold(unittest.TestCase):
    def test_catalog_constants(self):
        self.assertEqual(M.DISCARD, {"developer", "qa", "pm"})
        self.assertIn("Architecture", M.NEW_ROLES)
        self.assertIn("PM", M.NEW_ROLES)
        # Architecture sources both its skills from 系统架构师
        self.assertEqual(M.NEW_ROLES["Architecture"],
                         [("系统架构师", "design-architecture"), ("系统架构师", "tech-stack-review")])
        # PM update-board comes from the (to-be-discarded) pm dir
        self.assertIn(("pm", "update-board"), M.NEW_ROLES["PM"])
        self.assertEqual(M.EN_DISPLAY["系统架构师"], "System Architect")

    def test_synth_role_yaml_uses_db_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "系统架构师"
            M.synth_role_yaml(d, "系统架构师",
                              {"system_prompt": "你是架构师", "avatar_color": "#8b5cf6"})
            from skills.role_meta import read_role_meta
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "系统架构师")
            self.assertEqual(meta["avatar_color"], "#8b5cf6")
            self.assertEqual(meta["system_prompt"], "你是架构师")

    def test_synth_role_yaml_new_role_uses_new_role_meta_avatar(self):
        # Architecture/PM have no role_templates row (db_meta is None) but must
        # still get their NEW_ROLE_META default avatar; system_prompt stays None.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Architecture"
            M.synth_role_yaml(d, "Architecture", None)
            from skills.role_meta import read_role_meta
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "Architecture")
            self.assertEqual(meta["avatar_color"], "#8b5cf6")
            self.assertIsNone(meta["system_prompt"])

    def test_synth_role_yaml_minimal_for_unknown_role(self):
        # A role with neither db_meta nor a NEW_ROLE_META entry (e.g. step C's
        # auto-created empty role) → only display_name, everything else None.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "CEO"
            M.synth_role_yaml(d, "CEO", None)
            from skills.role_meta import read_role_meta
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "CEO")
            self.assertIsNone(meta["avatar_color"])
            self.assertIsNone(meta["system_prompt"])

    def test_main_dryrun_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = M.main([])           # no --apply
                self.assertEqual(rc, 0)
                self.assertEqual(list(root.iterdir()), [])  # nothing written
                self.assertIn("DRY-RUN", buf.getvalue())


class TestStepA(unittest.TestCase):
    def _legacy(self, root: Path):
        # 造两个保留角色 + 一个 discard + PM 的源
        for role, skills in {
            "系统架构师": ["design-architecture", "tech-stack-review"],
            "需求分析师": ["write-spec", "write-user-story"],
            "pm": ["update-board", "write-spec"],         # discard 目录，但 PM 借其 update-board
        }.items():
            sd = root / "roles" / role / "skills"
            sd.mkdir(parents=True)
            for s in skills:
                (sd / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")

    def test_build_zh_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(root)
            db_meta = {"系统架构师": {"system_prompt": "你是架构师", "avatar_color": "#8b5cf6"}}
            rep = M.build_zh_templates(root, db_meta, dry_run=False)
            zh = root / "templates" / "zh" / "roles"
            # 保留角色拷过来了
            self.assertTrue((zh / "系统架构师" / "skills" / "design-architecture.md").exists())
            from skills.role_meta import read_role_meta
            self.assertEqual(read_role_meta(zh / "系统架构师")["system_prompt"], "你是架构师")
            # discard 目录没建成模板（用 built 列表 + 精确目录名判断，避免大小写不敏感 FS 上
            # "pm" 与新建的 "PM" 误判碰撞）
            self.assertNotIn("pm", rep["built"])
            zh_names = {p.name for p in zh.iterdir()}
            self.assertNotIn("pm", zh_names)
            self.assertIn("PM", zh_names)
            # 新角色 Architecture（源自系统架构师）+ PM（update-board 源自 pm）
            self.assertTrue((zh / "Architecture" / "skills" / "tech-stack-review.md").exists())
            self.assertTrue((zh / "PM" / "skills" / "update-board.md").exists())
            self.assertTrue((zh / "PM" / "skills" / "write-spec.md").exists())
            self.assertIn("Architecture", rep["built"])

    def test_build_zh_templates_dryrun_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(root)
            M.build_zh_templates(root, {}, dry_run=True)
            self.assertFalse((root / "templates").exists())


class TestStepA2(unittest.TestCase):
    def test_build_en_skeletons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 先有一个 zh 模板角色
            zsk = root / "templates" / "zh" / "roles" / "系统架构师" / "skills"
            zsk.mkdir(parents=True)
            (zsk / "design-architecture.md").write_text(
                "---\nname: design-architecture\ndescription: x\n---\n中文正文", encoding="utf-8")
            (root / "templates" / "zh" / "roles" / "系统架构师" / "role.yaml").write_text(
                "display_name: 系统架构师\navatar_color: '#8b5cf6'\n", encoding="utf-8")

            M.build_en_skeletons(root, dry_run=False)
            en = root / "templates" / "en" / "roles" / "系统架构师"
            from skills.role_meta import read_role_meta
            self.assertEqual(read_role_meta(en)["display_name"], "System Architect")
            body = (en / "skills" / "design-architecture.md").read_text(encoding="utf-8")
            self.assertIn("name: design-architecture", body)   # frontmatter preserved
            self.assertIn("TODO", body)                        # placeholder body
            self.assertNotIn("中文正文", body)                  # zh body NOT carried over

    def test_dryrun_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "templates" / "zh" / "roles" / "PM" / "skills").mkdir(parents=True)
            M.build_en_skeletons(root, dry_run=True)
            self.assertFalse((root / "templates" / "en").exists())


class TestStepB(unittest.TestCase):
    def test_seed_existing_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                # zh 模板里有一个角色
                tsk = root / "templates" / "zh" / "roles" / "代码助手" / "skills"
                tsk.mkdir(parents=True)
                (tsk / "code-review.md").write_text("---\nname: code-review\n---\nb", encoding="utf-8")
                rep = M.seed_existing_groups(root, [3, 4], dry_run=False)
                from workspace import layout
                self.assertTrue((layout.group_roles_dir(3) / "代码助手" / "skills" / "code-review.md").exists())
                self.assertTrue((layout.group_roles_dir(4) / "代码助手").exists())
                self.assertEqual(rep["seeded"][3], ["代码助手"])

    def test_dryrun_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                (root / "templates" / "zh" / "roles" / "PM" / "skills").mkdir(parents=True)
                M.seed_existing_groups(root, [3], dry_run=True)
                self.assertFalse((root / "group_3").exists())


class TestStepC(unittest.TestCase):
    def test_plan_hits_and_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                from workspace import layout
                (layout.group_roles_dir(3) / "需求分析师").mkdir(parents=True)
                bots = [(101, 3, "需求分析师"), (102, 3, "CEO"), (103, 4, "Tester")]
                plan = M.plan_bot_roles(root, bots)
                self.assertIn((101, 3, "需求分析师"), plan["ok"])
                self.assertIn((3, "CEO"), plan["create"])
                self.assertIn((4, "Tester"), plan["create"])

    def test_align_creates_empty_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                from workspace import layout
                bots = [(102, 3, "CEO")]
                M.align_bot_roles(root, bots, dry_run=False)
                cdir = layout.group_roles_dir(3) / "CEO"
                self.assertTrue((cdir / "skills").is_dir())
                self.assertEqual(list((cdir / "skills").iterdir()), [])   # empty skills
                from skills.role_meta import read_role_meta
                self.assertEqual(read_role_meta(cdir)["display_name"], "CEO")

    def test_align_dryrun_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                from workspace import layout
                M.align_bot_roles(root, [(102, 3, "CEO")], dry_run=True)
                self.assertFalse((layout.group_roles_dir(3) / "CEO").exists())


if __name__ == "__main__":
    unittest.main()
