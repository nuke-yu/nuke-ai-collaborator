# backend/tests/test_skill_store.py
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from skills.store import SkillStore
from skills import scope as S


class TestStore(unittest.TestCase):
    def _ws(self, tmp):
        return patch("skills.constants.WORKSPACE_ROOT", Path(tmp))

    def test_write_read_list_delete_group(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            st = SkillStore()
            sc = S.GroupScope(7)
            st.write(sc, "house-style", "---\nname: house-style\ndescription: x\n---\nbody")
            self.assertIn("house-style", st.read(sc, "house-style"))
            self.assertEqual([s["name"] for s in st.list(sc)], ["house-style"])
            st.delete(sc, "house-style")
            self.assertEqual(st.list(sc), [])

    def test_copy_template_to_role(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            st = SkillStore()
            src, dst = S.TemplateScope("zh", "dev"), S.RoleScope(7, "dev")
            st.write(src, "code-review", "---\nname: code-review\ndescription: x\n---\nb")
            st.copy(src, "code-review", dst)
            self.assertEqual([s["name"] for s in st.list(dst)], ["code-review"])

    def test_write_flags_high_privilege(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            st = SkillStore()
            res = st.write(S.BotScope(7, 9), "danger",
                           "---\nname: danger\n---\nuse run_shell to do x")
            self.assertIn("run_shell", res["high_privilege"])

    def test_reject_bad_name(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            with self.assertRaises(ValueError):
                SkillStore().write(S.GroupScope(7), "../evil", "x")

    def test_reject_bad_name_all_methods(self):
        # The _is_safe_name guard is a path-traversal defense; pin it on every
        # name-taking method so a future refactor can't silently drop one.
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            st, sc = SkillStore(), S.GroupScope(7)
            with self.assertRaises(ValueError):
                st.read(sc, "../evil")
            with self.assertRaises(ValueError):
                st.delete(sc, "../evil")
            with self.assertRaises(ValueError):
                st.copy(S.TemplateScope("zh", "dev"), "../evil", sc)


    def test_directory_skill_operations(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            st = SkillStore()
            src = S.GroupScope(7)
            dst = S.RoleScope(7, "dev")
            
            # 1. Create a directory skill in group scope manually
            dir_path = src.dir() / "folder-skill"
            dir_path.mkdir(parents=True)
            (dir_path / "SKILL.md").write_text("---\nname: folder-skill\n---\nbody", encoding="utf-8")
            (dir_path / "companion.txt").write_text("companion", encoding="utf-8")
            
            # 2. Read it
            self.assertIn("body", st.read(src, "folder-skill"))
            
            # 3. Copy it
            st.copy(src, "folder-skill", dst)
            self.assertTrue((dst.dir() / "folder-skill" / "SKILL.md").exists())
            self.assertTrue((dst.dir() / "folder-skill" / "companion.txt").exists())
            
            # 4. Write to it (edit)
            st.write(dst, "folder-skill", "---\nname: folder-skill\n---\nnew body")
            self.assertIn("new body", (dst.dir() / "folder-skill" / "SKILL.md").read_text(encoding="utf-8"))
            
            # 5. Delete it
            st.delete(dst, "folder-skill")
            self.assertFalse((dst.dir() / "folder-skill").exists())


if __name__ == "__main__":
    unittest.main()
