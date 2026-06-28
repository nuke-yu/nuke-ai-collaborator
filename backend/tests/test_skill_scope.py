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
            self.assertEqual(S.LearnedScope(7, 1018, "active").dir(), Path("/ws/group_7/bots/bot_1018/skills/learned/active"))
            self.assertEqual(S.LearnedScope(7, 1018, "draft").dir(), Path("/ws/group_7/bots/bot_1018/skills/learned/draft"))
            self.assertEqual(S.ExternalGlobalScope().dir(), Path("/ws/external/skills"))
            self.assertEqual(S.ExternalGroupScope(7).dir(), Path("/ws/group_7/external/skills"))

    def test_parse_descriptor(self):
        self.assertEqual(S.parse_descriptor("group:7"), S.GroupScope(7))
        self.assertEqual(S.parse_descriptor("role:7:dev"), S.RoleScope(7, "dev"))
        self.assertEqual(S.parse_descriptor("bot:7:1018"), S.BotScope(7, 1018))
        self.assertEqual(S.parse_descriptor("learned:7:1018:active"), S.LearnedScope(7, 1018, "active"))
        self.assertEqual(S.parse_descriptor("learned:7:1018:draft"), S.LearnedScope(7, 1018, "draft"))
        self.assertEqual(S.parse_descriptor("external_global"), S.ExternalGlobalScope())
        self.assertEqual(S.parse_descriptor("external_group:7"), S.ExternalGroupScope(7))

    def test_parse_invalid_raises(self):
        with self.assertRaises(ValueError):
            S.parse_descriptor("bogus:1")

    def test_parse_descriptor_rejects_traversal(self):
        for bad in ("role:7:../../evil", "role:7:a/b", "template:..:dev", "template:en:../x"):
            with self.assertRaises(ValueError):
                S.parse_descriptor(bad)

    def test_parse_descriptor_allows_capitalized_and_unicode_roles(self):
        self.assertEqual(S.parse_descriptor("template:en:PM"), S.TemplateScope("en", "PM"))
        self.assertEqual(S.parse_descriptor("role:7:Architecture"), S.RoleScope(7, "Architecture"))
        self.assertEqual(S.parse_descriptor("role:7:系统架构师"), S.RoleScope(7, "系统架构师"))
        self.assertEqual(S.parse_descriptor("template:zh:需求分析师"), S.TemplateScope("zh", "需求分析师"))


if __name__ == "__main__":
    unittest.main()
