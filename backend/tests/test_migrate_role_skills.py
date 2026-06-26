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


if __name__ == "__main__":
    unittest.main()
