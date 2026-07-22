"""create_group must mark new groups as unassigned (NULL assigned_worker_id) so
the Supervisor spreads them by modulo — even on an existing prod DB whose groups
table still has the legacy `DEFAULT 'w0'`. Otherwise every new group inherits
'w0' and piles onto worker w0 (the hotspot bug).
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import db.writer as _writer


class TestCreateGroupRouting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = tempfile.mktemp(suffix="_central.db")
        self._orig_db, self._orig_w = db.DB_PATH, _writer.DB_PATH
        db.DB_PATH = self.path
        _writer.DB_PATH = self.path
        # Simulate the LEGACY prod schema: groups.assigned_worker_id DEFAULT 'w0'.
        async with db.write_connect() as c:
            await c.execute(
                """CREATE TABLE groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    assigned_worker_id TEXT DEFAULT 'w0'
                )"""
            )
            await c.execute(
                """CREATE TABLE group_memberships (
                    user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                    role TEXT NOT NULL, PRIMARY KEY(user_id,group_id)
                )"""
            )
            await c.commit()

    async def asyncTearDown(self):
        await db.aclose_writer()
        db.DB_PATH, _writer.DB_PATH = self._orig_db, self._orig_w
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    async def test_new_group_is_unassigned_despite_legacy_default(self):
        from api.groups import create_group, CreateGroupRequest
        with patch("api.groups.init_group_workspace", new=AsyncMock()):
            res = await create_group(
                CreateGroupRequest(name="New Project"),
                user={"uid": 7, "sub": "owner"},
            )
        gid = res["id"]
        async with db.write_connect() as c:
            cur = await c.execute("SELECT assigned_worker_id FROM groups WHERE id = ?", (gid,))
            row = await cur.fetchone()
            cur = await c.execute(
                "SELECT role FROM group_memberships WHERE user_id=7 AND group_id=?", (gid,)
            )
            membership = await cur.fetchone()
        self.assertIsNone(row[0], "new group must be unassigned (NULL), not the 'w0' default")
        self.assertEqual(membership, ("owner",))


if __name__ == "__main__":
    unittest.main()
