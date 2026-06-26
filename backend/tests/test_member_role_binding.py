# backend/tests/test_member_role_binding.py
import unittest
import os
import sys
import shutil
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _db_writer
import workspace

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_role_binding.db")
TEST_WS = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "test_role_binding_ws"
database.DB_PATH = TEST_DB_PATH
_db_writer.DB_PATH = TEST_DB_PATH
workspace.WORKSPACE_ROOT = TEST_WS
import skills.constants as _skill_const
_skill_const.WORKSPACE_ROOT = TEST_WS

from main import app
from httpx import AsyncClient, ASGITransport
from workspace import layout
from skills.role_meta import write_role_meta


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _pin_paths():
    """Re-pin module-global paths at setUp time so cross-file import order can't
    leave db / layout (which live-read these globals) pointing at another suite."""
    database.DB_PATH = TEST_DB_PATH
    _db_writer.DB_PATH = TEST_DB_PATH
    workspace.WORKSPACE_ROOT = TEST_WS
    _skill_const.WORKSPACE_ROOT = TEST_WS


class TestMemberRoleBinding(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _pin_paths()
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        for p in (TEST_DB_PATH,):
            if os.path.exists(p):
                os.remove(p)
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)
        await database.init_db()
        async with _db_writer.write_connect() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (7, 'g7')")
            await db.commit()
        # Group 7 catalog: PM (with a system_prompt to snapshot)
        pm = layout.group_roles_dir(7) / "PM"
        (pm / "skills").mkdir(parents=True)
        write_role_meta(pm, {"display_name": "需求分析师", "system_prompt": "你是需求分析师"})

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)

    async def test_off_catalog_role_rejected_422_zh(self):
        async with _client() as c:
            r = await c.post("/api/groups/7/members",
                             json={"name": "b1", "type": "bot", "role": "Wizard"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Wizard", r.json()["detail"])

    async def test_valid_role_snapshots_system_prompt(self):
        async with _client() as c:
            r = await c.post("/api/groups/7/members",
                             json={"name": "b2", "type": "bot", "role": "PM"})
        self.assertEqual(r.status_code, 200)
        mid = r.json()["id"]
        async with database.get_db() as db:
            m = await database.get_member(db, mid)
        self.assertEqual(m["system_prompt"], "你是需求分析师")

    async def test_explicit_system_prompt_not_overwritten(self):
        async with _client() as c:
            r = await c.post("/api/groups/7/members",
                             json={"name": "b3", "type": "bot", "role": "PM",
                                   "system_prompt": "custom"})
        mid = r.json()["id"]
        async with database.get_db() as db:
            m = await database.get_member(db, mid)
        self.assertEqual(m["system_prompt"], "custom")

    async def test_human_skips_validation(self):
        async with _client() as c:
            r = await c.post("/api/groups/7/members",
                             json={"name": "alice", "type": "human", "role": "anything"})
        self.assertEqual(r.status_code, 200)
