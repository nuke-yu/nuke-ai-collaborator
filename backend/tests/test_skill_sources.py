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


if __name__ == "__main__":
    unittest.main()
