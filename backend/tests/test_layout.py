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
    def test_bot_dir_nested_under_group(self):
        # Phase 2: bot_dir(gid, bot_id) → group_{gid}/bots/bot_{id}
        self.assertEqual(layout.bot_dir(3, 7), WORKSPACE_ROOT / "group_3" / "bots" / "bot_7")

    def test_bot_dir_gid_none_uses_flat_shim(self):
        # 过渡垫片：Task 4-9 期间 gid=None 走旧扁平路径，保证系统可跑
        self.assertEqual(layout.bot_dir(None, 7), WORKSPACE_ROOT / "bot_7")

    def test_group_dir_and_shared(self):
        self.assertEqual(layout.group_dir(3), WORKSPACE_ROOT / "group_3")
        self.assertEqual(layout.group_shared_dir(3), WORKSPACE_ROOT / "group_3" / "shared")
        self.assertEqual(layout.group_runs_dir(3), WORKSPACE_ROOT / "group_3" / "runs")

    def test_layout_is_pure_no_mkdir(self):
        # Pure functions: calling them must not create anything on disk.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            with patch.object(layout, "WORKSPACE_ROOT", tmp_root):
                _ = layout.bot_dir(1, 1)
                _ = layout.group_shared_dir(1)
                _ = layout.group_runs_dir(1)
            self.assertEqual(list(tmp_root.iterdir()), [])


class TestLayoutDelegation(unittest.TestCase):
    """bot_workspace / bot_ws / group_workspace 收口委托 layout（零行为变化）。"""

    def test_bot_workspace_delegates_to_layout(self):
        import workspace
        # 过渡期 bot_workspace 仍只吃 bot_id，经 gid=None 垫片走扁平路径
        self.assertEqual(workspace.bot_workspace(7).resolve(), layout.bot_dir(None, 7).resolve())

    def test_skills_bot_ws_delegates_to_layout(self):
        from skills.constants import bot_ws
        self.assertEqual(bot_ws(7), layout.bot_dir(None, 7))

    def test_skills_group_ws_delegates_to_layout(self):
        from skills.constants import group_ws
        self.assertEqual(group_ws(3), layout.group_shared_dir(3))

    def test_group_workspace_delegates_to_layout(self):
        import workspace
        self.assertEqual(workspace.group_workspace(3).resolve(), layout.group_shared_dir(3).resolve())


if __name__ == "__main__":
    unittest.main()
