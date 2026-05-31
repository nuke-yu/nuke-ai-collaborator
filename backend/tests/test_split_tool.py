"""CELL-06: one-time legacy -> central + per-group splitter.

Builds a legacy single DB with two groups' worth of data, splits it, and verifies
the partition: central holds global tables, each group DB holds only its own rows
(direct group_id and indirect message_id/session_id), IDs are preserved, and row
counts are conserved with no cross-group leakage.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from db.split_tool import split_database


async def _count(path, table, where=""):
    async with db.connect(path) as c:
        cur = await c.execute(f"SELECT COUNT(*) FROM {table} {where}")
        return (await cur.fetchone())[0]


async def _ids(path, table, col="id", where=""):
    async with db.connect(path) as c:
        cur = await c.execute(f"SELECT {col} FROM {table} {where}")
        return {r[0] for r in await cur.fetchall()}


class TestSplitTool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.legacy = os.path.join(self.tmp, "chat.db")
        self.central = os.path.join(self.tmp, "central.db")
        self.group_root = os.path.join(self.tmp, "workspaces")
        self._orig = db.DB_PATH
        db.DB_PATH = self.legacy
        await db.init_db()
        async with db.write_connect(self.legacy) as c:
            await c.execute("INSERT INTO groups (id,name) VALUES (1,'g1'),(2,'g2')")
            await c.execute(
                "INSERT INTO members (id,group_id,name,type) VALUES "
                "(10,1,'h1','human'),(11,1,'dev','bot'),(20,2,'h2','human'),(21,2,'qa','bot')"
            )
            await c.execute(
                "INSERT INTO messages (id,group_id,member_id,content) VALUES "
                "(1,1,10,'g1-a'),(2,1,11,'g1-b'),(3,2,20,'g2-a')"
            )
            await c.execute(
                "INSERT INTO message_reactions (message_id,member_id,emoji) VALUES "
                "(1,10,'👍'),(3,20,'🎉')"
            )
            await c.execute(
                "INSERT INTO agent_sessions (id,bot_id,group_id) VALUES "
                "('s-g1',11,1),('s-g2',21,2)"
            )
            await c.execute(
                "INSERT INTO session_events (session_id,event_type) VALUES "
                "('s-g1','start'),('s-g2','start')"
            )
            await c.execute(
                "INSERT INTO tickets (ticket_id,group_id,title) VALUES "
                "('T-1',1,'fix'),('T-2',2,'test')"
            )
            await c.commit()
        await db.aclose_writer()

    async def asyncTearDown(self):
        await db.aclose_writer()
        db.DB_PATH = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gpath(self, gid):
        return os.path.join(self.group_root, f"group_{gid}", "chat.db")

    async def test_split_partitions_correctly(self):
        report = await split_database(self.legacy, self.central, self.group_root)

        # central: global tables copied whole
        self.assertEqual(await _count(self.central, "groups"), 2)
        self.assertEqual(await _count(self.central, "members"), 4)
        self.assertGreater(await _count(self.central, "role_templates"), 0)
        # central must NOT contain group activity
        central_tables = report["central"]
        self.assertNotIn("messages", central_tables)

        g1, g2 = self._gpath(1), self._gpath(2)

        # messages partitioned, ids preserved, row count conserved
        self.assertEqual(await _ids(g1, "messages"), {1, 2})
        self.assertEqual(await _ids(g2, "messages"), {3})
        self.assertEqual(
            await _count(g1, "messages") + await _count(g2, "messages"), 3
        )
        # no cross-group leakage
        self.assertEqual(await _count(g1, "messages", "WHERE group_id=2"), 0)

        # indirect partitions (by message_id / session_id)
        self.assertEqual(await _ids(g1, "message_reactions", "message_id"), {1})
        self.assertEqual(await _ids(g2, "message_reactions", "message_id"), {3})
        self.assertEqual(await _ids(g1, "session_events", "session_id"), {"s-g1"})
        self.assertEqual(await _ids(g2, "session_events", "session_id"), {"s-g2"})

        # direct group tables
        self.assertEqual(await _count(g1, "agent_sessions"), 1)
        self.assertEqual(await _count(g1, "tickets"), 1)
        self.assertEqual(await _count(g2, "tickets"), 1)

    async def test_group_db_has_no_central_tables(self):
        await split_database(self.legacy, self.central, self.group_root)
        async with db.connect(self._gpath(1)) as c:
            cur = await c.execute("SELECT name FROM sqlite_master WHERE name='members'")
            self.assertIsNone(await cur.fetchone())


if __name__ == "__main__":
    unittest.main()
