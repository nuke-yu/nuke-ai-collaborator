"""Plan B Task 8 — importer pipeline from a local repo dir (no network)."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
import skills.constants as _const
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


def _mk_skill(repo: Path, name: str, body_extra: str = ""):
    sd = repo / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nplatforms: posix\nversion: 2.0\n---\n{body_extra}",
        encoding="utf-8",
    )
    return sd


class TestImporter(unittest.TestCase):
    def setUp(self):
        self._orig_root = _const.WORKSPACE_ROOT
        self._tmp = tempfile.mkdtemp()
        _const.WORKSPACE_ROOT = Path(self._tmp)

    def tearDown(self):
        _const.WORKSPACE_ROOT = self._orig_root

    def test_classify_and_high_privilege_scan(self):
        from skills import importer
        repo = Path(tempfile.mkdtemp())
        sd = _mk_skill(repo, "deploy", body_extra="please run_shell the script")
        self.assertEqual(importer.scan_high_privilege(sd), "run_shell")
        from skills.metadata import parse_skill_meta
        self.assertEqual(importer.classify_platforms(parse_skill_meta(sd / "SKILL.md")), "posix")

    def test_import_lands_files_and_registers(self):
        from skills import importer, registry
        from workspace import layout
        repo = Path(tempfile.mkdtemp())
        _mk_skill(repo, "deploy")
        _mk_skill(repo, "Bad Name")  # space → unsafe → rejected

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            result = await importer.import_from_dir(
                repo, "global", registry.GLOBAL_GROUP_ID,
                "https://github.com/x/y", "main", "abc123", imported_by=1,
            )
            rows = await registry.list_external()
            return result, rows

        try:
            _db.DB_PATH = path
            result, rows = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        imported_names = {i["name"] for i in result["imported"]}
        self.assertEqual(imported_names, {"deploy"})
        self.assertTrue(any("Bad Name" in r["path"] for r in result["rejected"]))
        # File landed in the global pool
        self.assertTrue((layout.external_global_skills_dir() / "deploy" / "SKILL.md").exists())
        # Registry row written with provenance
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["commit_sha"], "abc123")
        self.assertEqual(rows[0]["platforms"], "posix")


if __name__ == "__main__":
    unittest.main()
