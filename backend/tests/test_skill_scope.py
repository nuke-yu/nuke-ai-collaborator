import unittest
from pathlib import Path
from unittest.mock import patch
from skills import scope as S


class TestScope(unittest.TestCase):
    def test_dirs(self):
        with patch("skills.constants.WORKSPACE_ROOT", Path("/ws")), \
             patch("skills.constants.SYSTEM_SKILLS_ROOT", Path("/ws/system/skills")):
            self.assertEqual(S.GroupScope(7).dir(), Path("/ws/group_7/shared/skills"))
            self.assertEqual(S.RoleScope(7, "dev").dir(), Path("/ws/group_7/roles/dev/skills"))
            self.assertEqual(S.TemplateScope("en", "PM").dir(), Path("/ws/templates/en/roles/PM/skills"))
            self.assertEqual(S.BotScope(7, 1018).dir(), Path("/ws/group_7/bots/bot_1018/skills/manual"))

    def test_parse_descriptor(self):
        self.assertEqual(S.parse_descriptor("group:7"), S.GroupScope(7))
        self.assertEqual(S.parse_descriptor("role:7:dev"), S.RoleScope(7, "dev"))
        self.assertEqual(S.parse_descriptor("bot:7:1018"), S.BotScope(7, 1018))

    def test_parse_invalid_raises(self):
        with self.assertRaises(ValueError):
            S.parse_descriptor("bogus:1")


if __name__ == "__main__":
    unittest.main()
