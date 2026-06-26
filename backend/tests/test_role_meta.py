import tempfile, unittest
from pathlib import Path
from skills.role_meta import read_role_meta, write_role_meta


class TestRoleMeta(unittest.TestCase):
    def test_write_then_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "系统架构师"
            write_role_meta(d, {
                "display_name": "系统架构师",
                "avatar_color": "#8b5cf6",
                "system_prompt": "你是本项目的系统架构师……",
            })
            self.assertTrue((d / "role.yaml").exists())
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "系统架构师")
            self.assertEqual(meta["avatar_color"], "#8b5cf6")
            self.assertIn("架构师", meta["system_prompt"])

    def test_read_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_role_meta(Path(tmp) / "nope"))

    def test_write_omits_none_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "PM"
            write_role_meta(d, {"display_name": "PM", "avatar_color": None, "extra": "x"})
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "PM")
            self.assertIsNone(meta["avatar_color"])     # not written → None on read
            raw = (d / "role.yaml").read_text(encoding="utf-8")
            self.assertNotIn("extra", raw)              # unknown field dropped
            self.assertNotIn("avatar_color", raw)       # None field dropped


if __name__ == "__main__":
    unittest.main()
