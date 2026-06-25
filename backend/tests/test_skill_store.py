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


if __name__ == "__main__":
    unittest.main()
