"""Single layout truth source (workspace/layout.py).

Phase 1: bot_dir returns the current flat path (workspaces/bot_{id}), zero
behaviour change. layout is pure — no I/O, no mkdir.
"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from skills.constants import WORKSPACE_ROOT
from workspace import layout


class TestLayoutPhase1(unittest.TestCase):
    def test_bot_dir_flat_current_path(self):
        # Phase 1: bot_dir still returns the current flat path.
        self.assertEqual(layout.bot_dir(7), WORKSPACE_ROOT / "bot_7")

    def test_group_dir_and_shared(self):
        self.assertEqual(layout.group_dir(3), WORKSPACE_ROOT / "group_3")
        self.assertEqual(layout.group_shared_dir(3), WORKSPACE_ROOT / "group_3" / "shared")
        self.assertEqual(layout.group_runs_dir(3), WORKSPACE_ROOT / "group_3" / "runs")

    def test_layout_is_pure_no_mkdir(self):
        # Pure functions: calling them must not create anything on disk.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            with patch.object(layout, "WORKSPACE_ROOT", tmp_root):
                _ = layout.bot_dir(1)
                _ = layout.group_shared_dir(1)
                _ = layout.group_runs_dir(1)
            self.assertEqual(list(tmp_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
