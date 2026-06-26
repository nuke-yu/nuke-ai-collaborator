# backend/tests/test_skills_api.py
import unittest
import os
import sys
import shutil
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _db_writer
import workspace

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_skills_api.db")
TEST_WS = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "test_skills_api_ws"
database.DB_PATH = TEST_DB_PATH
_db_writer.DB_PATH = TEST_DB_PATH
workspace.WORKSPACE_ROOT = TEST_WS
import skills.constants as _skill_const
_skill_const.WORKSPACE_ROOT = TEST_WS

from main import app
from httpx import AsyncClient, ASGITransport
from workspace import layout


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestSkillsReadApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)
        # Seed a group role skill: group_7/roles/PM/skills/write-spec.md
        d = layout.group_roles_dir(7) / "PM" / "skills"
        d.mkdir(parents=True)
        (d / "write-spec.md").write_text("---\nname: write-spec\n---\nspec body", encoding="utf-8")

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)

    async def test_list_role_scope(self):
        async with _client() as c:
            r = await c.get("/api/skills", params={"scope": "role:7:PM"})
        self.assertEqual(r.status_code, 200)
        names = [s["name"] for s in r.json()["skills"]]
        self.assertIn("write-spec", names)

    async def test_read_skill_content(self):
        async with _client() as c:
            r = await c.get("/api/skills/content", params={"scope": "role:7:PM", "name": "write-spec"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("spec body", r.json()["content"])

    async def test_bad_descriptor_400(self):
        async with _client() as c:
            r = await c.get("/api/skills", params={"scope": "role:7:../etc"})
        self.assertEqual(r.status_code, 400)

    async def test_missing_skill_404(self):
        async with _client() as c:
            r = await c.get("/api/skills/content", params={"scope": "role:7:PM", "name": "nope"})
        self.assertEqual(r.status_code, 404)


class TestSkillsWriteApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)
        d = layout.templates_roles_dir("zh") / "PM" / "skills"
        d.mkdir(parents=True)
        (d / "write-spec.md").write_text("---\nname: write-spec\n---\nspec", encoding="utf-8")

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)

    async def test_write_then_read(self):
        async with _client() as c:
            w = await c.post("/api/skills", json={
                "scope": "group:7", "name": "house-rule", "content": "---\nname: house-rule\n---\nbe nice"})
            self.assertEqual(w.status_code, 200)
            r = await c.get("/api/skills/content", params={"scope": "group:7", "name": "house-rule"})
        self.assertIn("be nice", r.json()["content"])

    async def test_copy_template_to_role(self):
        async with _client() as c:
            cp = await c.post("/api/skills/copy", json={
                "src": "template:zh:PM", "name": "write-spec", "dst": "role:7:PM"})
            self.assertEqual(cp.status_code, 200)
            r = await c.get("/api/skills", params={"scope": "role:7:PM"})
        self.assertIn("write-spec", [s["name"] for s in r.json()["skills"]])

    async def test_copy_missing_source_404(self):
        async with _client() as c:
            cp = await c.post("/api/skills/copy", json={
                "src": "template:zh:PM", "name": "ghost", "dst": "role:7:PM"})
        self.assertEqual(cp.status_code, 404)

    async def test_delete_is_idempotent(self):
        async with _client() as c:
            await c.post("/api/skills", json={"scope": "group:7", "name": "tmp", "content": "x"})
            d1 = await c.delete("/api/skills", params={"scope": "group:7", "name": "tmp"})
            d2 = await c.delete("/api/skills", params={"scope": "group:7", "name": "tmp"})
        self.assertEqual(d1.status_code, 200)
        self.assertEqual(d2.status_code, 200)


class TestRoleCatalogApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)
        from skills.role_meta import write_role_meta
        tpl = layout.templates_roles_dir("zh") / "PM"
        (tpl / "skills").mkdir(parents=True)
        (tpl / "skills" / "write-spec.md").write_text("---\nname: write-spec\n---\nx", encoding="utf-8")
        write_role_meta(tpl, {"display_name": "需求分析师", "avatar_color": "#0ea5e9"})
        grp = layout.group_roles_dir(7) / "PM"
        (grp / "skills").mkdir(parents=True)
        write_role_meta(grp, {"display_name": "需求分析师", "avatar_color": "#0ea5e9"})

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)

    async def test_template_roles_default_lang_zh(self):
        async with _client() as c:
            r = await c.get("/api/templates/roles")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["lang"], "zh")
        pm = next(x for x in body["roles"] if x["role"] == "PM")
        self.assertEqual(pm["display_name"], "需求分析师")
        self.assertEqual(pm["skill_count"], 1)

    async def test_group_roles(self):
        async with _client() as c:
            r = await c.get("/api/groups/7/roles")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["role"] for x in r.json()["roles"]], ["PM"])


if __name__ == "__main__":
    unittest.main()
