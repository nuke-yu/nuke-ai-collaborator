import unittest
import tempfile
from pathlib import Path

from skills.role_catalog import list_role_catalog
from skills.role_meta import write_role_meta


class TestListRoleCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _role(self, name, *, skills=(), meta=None):
        d = self.root / name
        (d / "skills").mkdir(parents=True)
        for s in skills:
            (d / "skills" / f"{s}.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
        if meta is not None:
            write_role_meta(d, meta)

    def test_missing_root_returns_empty(self):
        self.assertEqual(list_role_catalog(self.root / "nope"), [])

    def test_lists_roles_sorted_with_meta_and_count(self):
        self._role("系统架构师", skills=["design-architecture", "tech-stack-review"],
                   meta={"display_name": "系统架构师", "avatar_color": "#8b5cf6",
                         "system_prompt": "你是架构师"})
        self._role("PM", skills=["write-spec"], meta={"avatar_color": "#0ea5e9"})
        rows = list_role_catalog(self.root)
        self.assertEqual([r["role"] for r in rows], ["PM", "系统架构师"])  # sorted
        pm, arch = rows[0], rows[1]
        self.assertEqual(pm["display_name"], "PM")          # falls back to dir name
        self.assertEqual(pm["avatar_color"], "#0ea5e9")
        self.assertIsNone(pm["system_prompt"])
        self.assertEqual(pm["skill_count"], 1)
        self.assertEqual(arch["display_name"], "系统架构师")
        self.assertEqual(arch["skill_count"], 2)

    def test_role_without_skills_dir_counts_zero(self):
        (self.root / "Empty").mkdir(parents=True)
        rows = list_role_catalog(self.root)
        self.assertEqual(rows[0]["skill_count"], 0)

    def test_non_dir_entries_ignored(self):
        (self.root / "stray.txt").write_text("x", encoding="utf-8")
        self._role("PM", skills=["write-spec"])
        rows = list_role_catalog(self.root)
        self.assertEqual([r["role"] for r in rows], ["PM"])

    def test_display_name_resolves_by_lang(self):
        self._role("系统架构师", meta={"display_name": "系统架构师",
                                  "display_name_en": "System Architect"})
        # role identity (dir name) is language-neutral; only display switches
        zh = list_role_catalog(self.root, "zh")[0]
        en = list_role_catalog(self.root, "en")[0]
        self.assertEqual(zh["role"], "系统架构师")
        self.assertEqual(en["role"], "系统架构师")
        self.assertEqual(zh["display_name"], "系统架构师")
        self.assertEqual(en["display_name"], "System Architect")

    def test_lang_en_falls_back_when_no_en_name(self):
        # no display_name_en → en falls back to base display_name, then dir name
        self._role("代码助手", meta={"display_name": "代码助手"})
        self._role("CEO")  # no role.yaml at all
        rows = {r["role"]: r["display_name"] for r in list_role_catalog(self.root, "en")}
        self.assertEqual(rows["代码助手"], "代码助手")  # base display_name
        self.assertEqual(rows["CEO"], "CEO")          # dir name

    def test_default_lang_is_zh(self):
        self._role("系统架构师", meta={"display_name": "系统架构师",
                                  "display_name_en": "System Architect"})
        self.assertEqual(list_role_catalog(self.root)[0]["display_name"], "系统架构师")


if __name__ == "__main__":
    unittest.main()
