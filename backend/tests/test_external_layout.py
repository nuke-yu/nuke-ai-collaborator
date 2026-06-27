"""Plan B Task 1 — external pool layout dirs."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skills.constants as _const
from workspace import layout


class TestExternalLayout(unittest.TestCase):
    def test_global_and_group_external_dirs(self):
        root = _const.WORKSPACE_ROOT
        self.assertEqual(layout.external_global_skills_dir(), root / "external" / "skills")
        self.assertEqual(layout.group_external_skills_dir(7), root / "group_7" / "external" / "skills")

    def test_dirs_are_pure_no_mkdir(self):
        # Calling them must not create anything on disk.
        p = layout.group_external_skills_dir(999999)
        self.assertFalse(p.exists())


if __name__ == "__main__":
    unittest.main()
