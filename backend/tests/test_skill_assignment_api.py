"""Plan B Task 10 — assignment endpoints reconcile bot_skills."""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


class TestAssignmentReconcile(unittest.TestCase):
    def test_put_upserts_and_removes(self):
        # Drive the reconcile helper directly (route body extracted to a testable fn).
        from api.groups import _reconcile_bot_skills
        from skills import assignment

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type) VALUES (5,1,'dev','bot')")
                await conn.commit()
            await assignment.set_assignment(5, "old", "external_global", enabled=True)
            # New desired state: keep 'deploy', drop 'old'
            await _reconcile_bot_skills(5, [
                {"name": "deploy", "pool": "external_global", "enabled": True},
            ])
            return await assignment.list_assignments(5)

        try:
            _db.DB_PATH = path
            rows = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        names = {r["skill_name"] for r in rows}
        self.assertEqual(names, {"deploy"})  # 'old' removed, 'deploy' added

    def test_api_cross_group_enforcement(self):
        from main import app
        from httpx import AsyncClient, ASGITransport
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g1'), (2, 'g2')")
                # Bot 5 in Group 1
                await conn.execute("INSERT INTO members (id, group_id, name, type) VALUES (5,1,'bot1','bot')")
                # Bot 6 in Group 2
                await conn.execute("INSERT INTO members (id, group_id, name, type) VALUES (6,2,'bot2','bot')")
                await conn.commit()

            from core.auth import create_token
            token = create_token(1, "tester")
            headers = {"Authorization": f"Bearer {token}"}

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                # GET bot 5 under group 1 -> 200 OK
                res = await ac.get("/api/groups/1/members/5/skills", headers=headers)
                self.assertEqual(res.status_code, 200)

                # GET bot 6 under group 1 (cross-group) -> 404 Not Found
                res = await ac.get("/api/groups/1/members/6/skills", headers=headers)
                self.assertEqual(res.status_code, 404)

                # PUT bot 6 under group 1 (cross-group) -> 404 Not Found
                res = await ac.put("/api/groups/1/members/6/skills", json={"assigned": []}, headers=headers)
                self.assertEqual(res.status_code, 404)

                # PUT bot 5 under group 1 -> 200 OK
                res = await ac.put("/api/groups/1/members/5/skills", json={"assigned": []}, headers=headers)
                self.assertEqual(res.status_code, 200)

        try:
            _db.DB_PATH = path
            _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
