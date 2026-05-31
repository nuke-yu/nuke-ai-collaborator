"""
DFT-059 regression: workspace/_get_effective_ws redirected shared files
(BOARD.md / SPEC.md / API_CONTRACT.md / deliverables/) by calling group_ws(),
a name that does not exist in the module (the function is group_workspace).
Any real bot reading/writing a shared file hit NameError -> HTTP 500, breaking
the group collaboration path and the frontend Workspace panel.

These tests pin the redirect to group_workspace and confirm private files stay
in the bot's own workspace.
"""
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from workspace import _get_effective_ws


def _fake_connect_sync(group_id):
    """A context manager mimicking db.connect_sync() whose query returns group_id."""
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (group_id,)
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm


class TestWorkspaceRedirect(unittest.TestCase):
    def test_shared_file_redirects_to_group_workspace(self):
        for shared in ("BOARD.md", "SPEC.md", "API_CONTRACT.md", "deliverables/app.py"):
            with self.subTest(path=shared):
                with patch("workspace.bot_workspace", return_value=Path("/tmp/bot7")), \
                     patch("workspace.group_workspace", return_value=Path("/tmp/group42")) as gw, \
                     patch("db.connect_sync", return_value=_fake_connect_sync(42)):
                    # Before the fix this raised NameError: name 'group_ws' is not defined
                    result = _get_effective_ws(7, shared)
                self.assertEqual(result, Path("/tmp/group42"))
                gw.assert_called_once_with(42)

    def test_private_file_stays_in_bot_workspace(self):
        with patch("workspace.bot_workspace", return_value=Path("/tmp/bot7")), \
             patch("workspace.group_workspace") as gw:
            result = _get_effective_ws(7, "notes.md")
        self.assertEqual(result, Path("/tmp/bot7"))
        gw.assert_not_called()

    def test_shared_file_with_no_member_row_falls_back_to_bot_ws(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None  # member not found
        cm = MagicMock()
        cm.__enter__.return_value = conn
        with patch("workspace.bot_workspace", return_value=Path("/tmp/bot7")), \
             patch("workspace.group_workspace") as gw, \
             patch("db.connect_sync", return_value=cm):
            result = _get_effective_ws(7, "BOARD.md")
        self.assertEqual(result, Path("/tmp/bot7"))
        gw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
