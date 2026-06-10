"""watcher 路径解析：bot 私有技能现位于 group_{gid}/bots/bot_{id}/skills/。"""
import unittest

from skills.constants import WORKSPACE_ROOT
from skills.watcher import _parse_path


class TestWatcherParse(unittest.TestCase):
    def test_bot_skill_path_parses_group_and_member(self):
        abs_p = str(WORKSPACE_ROOT / "group_3" / "bots" / "bot_7" / "skills" / "foo.md")
        self.assertEqual(_parse_path(abs_p),
                         {"source": "bot", "member_id": 7, "group_id": 3})

    def test_group_shared_skill_still_parses(self):
        abs_p = str(WORKSPACE_ROOT / "group_3" / "shared" / "skills" / "bar.md")
        self.assertEqual(_parse_path(abs_p),
                         {"source": "group", "member_id": None, "group_id": 3})

    def test_old_flat_bot_path_no_longer_matches_bot(self):
        # 旧扁平 bot_{id}/skills 不再被识别为 bot 技能
        abs_p = str(WORKSPACE_ROOT / "bot_7" / "skills" / "foo.md")
        self.assertIsNone(_parse_path(abs_p))


if __name__ == "__main__":
    unittest.main()
