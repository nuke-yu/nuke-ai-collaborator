"""Plan B follow-up — GET member skills joins SKILL.md description into the pool."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


class TestDescriptionJoin(unittest.TestCase):
    def test_pool_rows_get_description_from_skill_md(self):
        from api import groups
        from skills import registry

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        tmp_global = tempfile.mkdtemp()
        orig = _db.DB_PATH

        # Lay a global-scope skill on disk: <global_dir>/deploy/SKILL.md
        skill_dir = Path(tmp_global) / "deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: Ship the build to prod\n---\nbody\n",
            encoding="utf-8",
        )

        async def go():
            await init_central_db(path)
            await registry.register(
                "deploy", "global", 0, "https://github.com/x/y", "main",
                "abc123", "1.0.0", "pure", "", None,
            )
            pool = await registry.list_external("global")
            return groups._attach_descriptions(pool)

        try:
            _db.DB_PATH = path
            with patch("api.groups.layout.external_global_skills_dir",
                       return_value=Path(tmp_global)):
                pool = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)
            import shutil
            shutil.rmtree(tmp_global, ignore_errors=True)

        row = next(r for r in pool if r["name"] == "deploy")
        self.assertEqual(row["description"], "Ship the build to prod")

    def test_missing_skill_md_yields_empty_description(self):
        from api import groups

        # scope_kind=group, but no file on disk → empty string, no crash.
        with patch("api.groups.layout.group_external_skills_dir",
                   return_value=Path(tempfile.mkdtemp())):
            pool = groups._attach_descriptions(
                [{"name": "ghost", "scope_kind": "group", "group_id": 7}]
            )
        self.assertEqual(pool[0]["description"], "")


if __name__ == "__main__":
    unittest.main()
