"""Plan B Task 9 — import/remove via importer wrapper + host allowlist."""
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


def _mk_skill(repo: Path, name: str):
    sd = repo / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody", encoding="utf-8")


class TestCloneAndImport(unittest.TestCase):
    def setUp(self):
        self._orig_root = _const.WORKSPACE_ROOT
        self._tmp = tempfile.mkdtemp()
        _const.WORKSPACE_ROOT = Path(self._tmp)

    def tearDown(self):
        _const.WORKSPACE_ROOT = self._orig_root

    def test_disallowed_host_rejected(self):
        from skills import importer
        with self.assertRaises(ValueError):
            _run(importer.clone_and_import(
                "https://evil.example.com/x/y", "main", "global", 0, 1,
                _clone=lambda url, ref, dst: None,
            ))

    def test_disallowed_ssh_host_rejected(self):
        from skills import importer
        with self.assertRaises(ValueError):
            _run(importer.clone_and_import(
                "git@evil.example.com:x/y.git", "main", "global", 0, 1,
                _clone=lambda url, ref, dst: None,
            ))

    def test_allowed_host_imports_via_injected_clone(self):
        from skills import importer, registry
        repo = Path(tempfile.mkdtemp())
        _mk_skill(repo, "deploy")

        def fake_clone(url, ref, dst):
            # Simulate `git clone` by copying our fixture into dst.
            import shutil
            shutil.copytree(repo, dst, dirs_exist_ok=True)
            return "deadbeef"

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            result = await importer.clone_and_import(
                "https://github.com/x/y", "main", "global", 0, 1, _clone=fake_clone,
            )
            rows = await registry.list_external()
            return result, rows

        try:
            _db.DB_PATH = path
            result, rows = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        self.assertEqual({i["name"] for i in result["imported"]}, {"deploy"})
        self.assertEqual(rows[0]["commit_sha"], "deadbeef")


if __name__ == "__main__":
    unittest.main()
