"""Plan B Task 5 — external pool flows through discovery (unfiltered)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skills.constants as _const


def _write_skill(pool_dir: Path, name: str):
    sd = pool_dir / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody", encoding="utf-8")


class TestExternalDiscovery(unittest.TestCase):
    def setUp(self):
        self._orig_root = _const.WORKSPACE_ROOT
        self._orig_sys = _const.SYSTEM_SKILLS_ROOT
        self._tmp = tempfile.mkdtemp()
        _const.WORKSPACE_ROOT = Path(self._tmp)
        _const.SYSTEM_SKILLS_ROOT = Path(self._tmp) / "system" / "skills"
        from skills.discovery import invalidate_skills_cache
        invalidate_skills_cache()

    def tearDown(self):
        _const.WORKSPACE_ROOT = self._orig_root
        _const.SYSTEM_SKILLS_ROOT = self._orig_sys
        from skills.discovery import invalidate_skills_cache
        invalidate_skills_cache()

    def test_external_skill_listed_and_tagged(self):
        from workspace import layout
        from skills.discovery import _compute_skills_all
        _write_skill(layout.external_global_skills_dir(), "deploy")
        skills = _compute_skills_all(bot_id=1, group_id=2, role=None)
        by_name = {s["name"]: s for s in skills}
        self.assertIn("deploy", by_name)
        self.assertEqual(by_name["deploy"]["layer"], "external_global")

    def test_signature_sensitive_to_external_changes(self):
        from workspace import layout
        from skills.discovery import _scan_signature
        sig_before = _scan_signature(bot_id=1, group_id=2, role=None)
        _write_skill(layout.external_global_skills_dir(), "deploy")
        self.assertNotEqual(_scan_signature(bot_id=1, group_id=2, role=None), sig_before)


if __name__ == "__main__":
    unittest.main()
