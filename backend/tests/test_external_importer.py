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


    def test_parse_git_url_subdir(self):
        from skills.importer import parse_git_url_subdir

        # Basic GitHub HTTP URL
        clone, ref, subdir = parse_git_url_subdir("https://github.com/phuryn/pm-skills")
        self.assertEqual(clone, "https://github.com/phuryn/pm-skills.git")
        self.assertEqual(ref, "")
        self.assertEqual(subdir, "")

        # GitHub URL with tree/branch and subdirectory
        clone, ref, subdir = parse_git_url_subdir(
            "https://github.com/phuryn/pm-skills/tree/main/pm-product-discovery"
        )
        self.assertEqual(clone, "https://github.com/phuryn/pm-skills.git")
        self.assertEqual(ref, "main")
        self.assertEqual(subdir, "pm-product-discovery")

        # GitHub URL with branch containing slashes
        clone, ref, subdir = parse_git_url_subdir(
            "https://github.com/phuryn/pm-skills/tree/feature/new-skills/pm-product-discovery",
            ref="feature/new-skills"
        )
        self.assertEqual(clone, "https://github.com/phuryn/pm-skills.git")
        self.assertEqual(ref, "feature/new-skills")
        self.assertEqual(subdir, "pm-product-discovery")

        # Schemeless URL
        clone, ref, subdir = parse_git_url_subdir(
            "github.com/phuryn/pm-skills/tree/main/pm-product-discovery"
        )
        self.assertEqual(clone, "https://github.com/phuryn/pm-skills.git")
        self.assertEqual(ref, "main")
        self.assertEqual(subdir, "pm-product-discovery")

        # GitLab URL with slash-dash
        clone, ref, subdir = parse_git_url_subdir(
            "https://gitlab.com/phuryn/pm-skills/-/tree/main/pm-product-discovery"
        )
        self.assertEqual(clone, "https://gitlab.com/phuryn/pm-skills.git")
        self.assertEqual(ref, "main")
        self.assertEqual(subdir, "pm-product-discovery")

        # GitHub file blob URL
        clone, ref, subdir = parse_git_url_subdir(
            "https://github.com/phuryn/pm-skills/blob/main/pm-product-discovery/skills/prioritize-features/SKILL.md"
        )
        self.assertEqual(clone, "https://github.com/phuryn/pm-skills.git")
        self.assertEqual(ref, "main")
        self.assertEqual(subdir, "pm-product-discovery/skills/prioritize-features/SKILL.md")

    def test_import_from_dir_with_subdir(self):
        from skills import importer, registry
        repo = Path(tempfile.mkdtemp())
        _mk_skill(repo, "git-helper") # outside subdir
        sub = repo / "my-sub"
        _mk_skill(sub, "deploy") # inside subdir

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            # import from my-sub subdir
            result = await importer.import_from_dir(
                repo, "global", registry.GLOBAL_GROUP_ID,
                "https://github.com/x/y", "main", "abc123", imported_by=1,
                subdir="my-sub"
            )
            return result

        try:
            _db.DB_PATH = path
            result = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        imported_names = {i["name"] for i in result["imported"]}
        # Only deploy should be imported since it is inside "my-sub"
        self.assertEqual(imported_names, {"deploy"})
        self.assertNotIn("git-helper", imported_names)

    def test_import_from_dir_unsafe_subdir_traversal(self):
        from skills import importer
        repo = Path(tempfile.mkdtemp())

        async def go():
            await importer.import_from_dir(
                repo, "global", 0, "https://github.com/x/y", "main", "abc", 1,
                subdir="../unsafe"
            )

        with self.assertRaises(ValueError) as ctx:
            _run(go())
        self.assertIn("unsafe subdirectory path", str(ctx.exception))


    def test_import_from_dir_with_file_target(self):
        from skills import importer, registry
        repo = Path(tempfile.mkdtemp())
        sub = repo / "my-sub"
        skill_dir = _mk_skill(sub, "deploy")
        file_path = skill_dir / "SKILL.md"

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            # import pointing directly to the file
            result = await importer.import_from_dir(
                repo, "global", registry.GLOBAL_GROUP_ID,
                "https://github.com/x/y", "main", "abc123", imported_by=1,
                subdir="my-sub/deploy/SKILL.md"
            )
            return result

        try:
            _db.DB_PATH = path
            result = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        imported_names = {i["name"] for i in result["imported"]}
        self.assertEqual(imported_names, {"deploy"})


    def test_import_from_dir_with_root_level_skill(self):
        from skills import importer, registry
        repo = Path(tempfile.mkdtemp())
        
        # Write SKILL.md directly at the root of the repository
        (repo / "SKILL.md").write_text(
            "---\nname: ignore-this-name\ndescription: bill gates skill description\n---\nbody",
            encoding="utf-8"
        )

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            # import pointing directly to the root of the repository
            result = await importer.import_from_dir(
                repo, "global", registry.GLOBAL_GROUP_ID,
                "https://github.com/OpenDemon/bill-gates-skill.git", "", "abc123", imported_by=1
            )
            return result

        try:
            _db.DB_PATH = path
            result = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        imported_names = {i["name"] for i in result["imported"]}
        # Name should be resolved to the repository name "bill-gates-skill"
        self.assertEqual(imported_names, {"bill-gates-skill"})


if __name__ == "__main__":
    unittest.main()
