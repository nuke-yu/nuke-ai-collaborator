"""Product-grade: mtime-invalidated cache over skill frontmatter parsing.

`parse_skill_meta_cached` serves repeated reads of an unchanged SKILL.md from
cache and only re-parses when the file's mtime moves. Backs the member-skills
GET description join, which is read on every panel open."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMetaCache(unittest.TestCase):
    def setUp(self):
        from skills import metadata
        metadata._META_CACHE.clear()
        self.dir = tempfile.mkdtemp()
        self.path = Path(self.dir) / "SKILL.md"
        self.path.write_text(
            "---\nname: x\ndescription: first\n---\nbody\n", encoding="utf-8"
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_unchanged_file_is_served_from_cache(self):
        from skills import metadata
        calls = {"n": 0}
        real = metadata.parse_skill_meta

        def counting(path):
            calls["n"] += 1
            return real(path)

        with patch.object(metadata, "parse_skill_meta", counting):
            m1 = metadata.parse_skill_meta_cached(self.path)
            m2 = metadata.parse_skill_meta_cached(self.path)

        self.assertEqual(m1["description"], "first")
        self.assertEqual(m2["description"], "first")
        self.assertEqual(calls["n"], 1)  # second read hit the cache, no re-parse

    def test_reparses_when_mtime_changes(self):
        from skills import metadata
        calls = {"n": 0}
        real = metadata.parse_skill_meta

        def counting(path):
            calls["n"] += 1
            return real(path)

        with patch.object(metadata, "parse_skill_meta", counting):
            first = metadata.parse_skill_meta_cached(self.path)
            self.assertEqual(first["description"], "first")
            # Rewrite + force a distinct mtime (avoid coarse-fs collisions).
            self.path.write_text(
                "---\nname: x\ndescription: second\n---\nbody\n", encoding="utf-8"
            )
            future = time.time_ns() + 10_000_000_000
            os.utime(self.path, ns=(future, future))
            second = metadata.parse_skill_meta_cached(self.path)

        self.assertEqual(second["description"], "second")
        self.assertEqual(calls["n"], 2)  # mtime moved -> re-parsed

    def test_missing_file_returns_empty_and_is_not_cached(self):
        from skills import metadata
        ghost = Path(self.dir) / "gone" / "SKILL.md"
        meta = metadata.parse_skill_meta_cached(ghost)
        self.assertEqual(meta["description"], "")
        self.assertNotIn(str(ghost), metadata._META_CACHE)


if __name__ == "__main__":
    unittest.main()
