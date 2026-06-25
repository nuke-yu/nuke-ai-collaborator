import unittest
from pathlib import Path
from unittest.mock import patch
from workspace import layout


class TestLayoutRolePaths(unittest.TestCase):
    def test_group_roles_dir(self):
        with patch("skills.constants.WORKSPACE_ROOT", Path("/ws")):
            self.assertEqual(layout.group_roles_dir(7), Path("/ws/group_7/roles"))

    def test_templates_roles_dir(self):
        with patch("skills.constants.WORKSPACE_ROOT", Path("/ws")):
            self.assertEqual(layout.templates_roles_dir("en"), Path("/ws/templates/en/roles"))


if __name__ == "__main__":
    unittest.main()
