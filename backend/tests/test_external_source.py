"""Plan B Task 3 — ExternalPoolSource enumerates global + group external pools."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skills.constants as _const


def _write_skill(pool_dir: Path, name: str, desc: str):
    sd = pool_dir / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody", encoding="utf-8")


class TestExternalSource(unittest.TestCase):
    def setUp(self):
        self._orig_root = _const.WORKSPACE_ROOT
        self._tmp = tempfile.mkdtemp()
        _const.WORKSPACE_ROOT = Path(self._tmp)

    def tearDown(self):
        _const.WORKSPACE_ROOT = self._orig_root

    def test_enumerates_both_layers_with_correct_tags(self):
        from workspace import layout
        from skills.sources.base import ScanCtx
        from skills.sources.external import ExternalPoolSource

        _write_skill(layout.external_global_skills_dir(), "deploy", "global one")
        _write_skill(layout.group_external_skills_dir(3), "lint", "group one")

        entries = ExternalPoolSource(ScanCtx(bot_id=1, group_id=3)).enumerate()
        by_name = {e["name"]: e for e in entries}
        self.assertEqual(by_name["deploy"]["layer"], "external_global")
        self.assertEqual(by_name["lint"]["layer"], "external_group")

    def test_group_pool_skipped_when_no_group_id(self):
        from workspace import layout
        from skills.sources.base import ScanCtx
        from skills.sources.external import ExternalPoolSource

        _write_skill(layout.external_global_skills_dir(), "deploy", "global one")
        _write_skill(layout.group_external_skills_dir(3), "lint", "group one")

        entries = ExternalPoolSource(ScanCtx(bot_id=1, group_id=None)).enumerate()
        names = {e["name"] for e in entries}
        self.assertEqual(names, {"deploy"})   # group pool not scanned without a group

    def test_signature_changes_when_a_skill_added(self):
        from workspace import layout
        from skills.sources.base import ScanCtx
        from skills.sources.external import ExternalPoolSource

        src = ExternalPoolSource(ScanCtx(bot_id=1, group_id=3))
        sig_before = src.signature()
        _write_skill(layout.external_global_skills_dir(), "deploy", "global one")
        self.assertNotEqual(src.signature(), sig_before)


if __name__ == "__main__":
    unittest.main()
