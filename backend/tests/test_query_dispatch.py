import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestIpcTypes(unittest.TestCase):
    def test_query_and_mutate_are_downstream(self):
        from runtime.ipc import protocol
        self.assertEqual(protocol.QUERY, "query")
        self.assertEqual(protocol.MUTATE, "mutate")
        self.assertIn(protocol.QUERY, protocol.DOWNSTREAM)
        self.assertIn(protocol.MUTATE, protocol.DOWNSTREAM)


class QueryDispatchBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import db
        self.path = tempfile.mktemp(suffix="_group.db")
        await db.init_group_db(self.path)
        # seed 3 messages directly in the group DB
        async with db.write_connect(self.path) as conn:
            for i in range(1, 4):
                await conn.execute(
                    "INSERT INTO messages (id, group_id, member_id, content, sender_name, sender_type, sender_avatar) "
                    "VALUES (?, 1, 5, ?, 'Nuke', 'human', '#fff')",
                    (i, f"msg{i}"),
                )
            await conn.commit()

    async def asyncTearDown(self):
        import db
        await db.aclose_writer()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    async def _run(self, msg):
        """Bind the seeded group DB and run dispatch_query with bus.broadcast captured."""
        import db
        from runtime import query_dispatch
        sent = []
        with db.bind_db(self.path):
            with patch.object(query_dispatch.bus, "broadcast",
                              new=AsyncMock(side_effect=lambda gid, p: sent.append((gid, p)))):
                await query_dispatch.dispatch_query(msg)
        return sent


class TestDispatchQueryMessages(QueryDispatchBase):
    async def test_messages_returns_history_and_has_more(self):
        sent = await self._run({"type": "query", "group_id": 1, "req_id": "c1-1",
                                "query": "messages", "limit": 2})
        self.assertEqual(len(sent), 1)
        gid, payload = sent[0]
        self.assertEqual(gid, 1)
        self.assertEqual(payload["type"], "query_result")
        self.assertEqual(payload["req_id"], "c1-1")
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]["messages"]), 2)
        self.assertTrue(payload["data"]["has_more"])      # 2 == limit → maybe more

    async def test_messages_before_id_paginates(self):
        sent = await self._run({"type": "query", "group_id": 1, "req_id": "c1-2",
                                "query": "messages", "before_id": 2, "limit": 50})
        ids = [m["id"] for m in sent[0][1]["data"]["messages"]]
        self.assertEqual(ids, [1])
        self.assertFalse(sent[0][1]["data"]["has_more"])


class TestDispatchQueryOther(QueryDispatchBase):
    async def test_search_matches_content_without_members_join(self):
        sent = await self._run({"type": "query", "group_id": 1, "req_id": "c1-3",
                                "query": "search", "q": "msg2", "limit": 30})
        rows = sent[0][1]["data"]
        self.assertEqual([r["id"] for r in rows], [2])
        self.assertEqual(rows[0]["sender_name"], "Nuke")
        self.assertEqual(rows[0]["avatar_color"], "#fff")

    async def test_search_blank_returns_empty(self):
        sent = await self._run({"type": "query", "group_id": 1, "req_id": "c1-4",
                                "query": "search", "q": "   "})
        self.assertEqual(sent[0][1]["data"], [])

    async def test_reactions_and_pins_empty_by_default(self):
        s1 = await self._run({"type": "query", "group_id": 1, "req_id": "c1-5", "query": "reactions"})
        self.assertEqual(s1[0][1]["data"], {})
        s2 = await self._run({"type": "query", "group_id": 1, "req_id": "c1-6", "query": "pins"})
        self.assertEqual(s2[0][1]["data"], [])


if __name__ == "__main__":
    unittest.main()
