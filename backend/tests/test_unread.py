import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


class UnreadDBBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # central DB owns unread_counts; init_central_db creates the schema
        self.path = tempfile.mktemp(suffix="_central.db")
        await db.init_central_db(self.path)

    async def asyncTearDown(self):
        await db.aclose_writer()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass


class TestUnreadCounts(UnreadDBBase):
    async def test_increment_then_get(self):
        async with db.write_connect(self.path) as conn:
            await db.increment_unread(conn, 1, 5, 1)
            await db.increment_unread(conn, 1, 5, 1)
            await db.increment_unread(conn, 2, 5, 1)
        async with db.connect(self.path) as conn:
            counts = await db.get_unread_counts(conn, 5)
        self.assertEqual(counts, {1: 2, 2: 1})

    async def test_reset_clears_one_group_only(self):
        async with db.write_connect(self.path) as conn:
            await db.increment_unread(conn, 1, 5, 3)
            await db.increment_unread(conn, 2, 5, 4)
            await db.reset_unread(conn, 1, 5)
        async with db.connect(self.path) as conn:
            counts = await db.get_unread_counts(conn, 5)
        self.assertEqual(counts.get(1), 0)
        self.assertEqual(counts.get(2), 4)

    async def test_reset_on_unseen_pair_is_noop_zero(self):
        async with db.write_connect(self.path) as conn:
            await db.reset_unread(conn, 9, 5)  # never incremented
        async with db.connect(self.path) as conn:
            self.assertEqual((await db.get_unread_counts(conn, 5)).get(9), 0)


class TestBumpForGroup(UnreadDBBase):
    def _members(self):
        return [
            {"id": 5, "type": "human"},   # sender
            {"id": 6, "type": "human"},   # other human, offline -> bump
            {"id": 7, "type": "human"},   # other human, online -> skip
            {"id": 8, "type": "bot"},     # bot -> never
        ]

    async def test_bumps_offline_humans_except_sender_and_online(self):
        async with db.write_connect(self.path) as conn:
            bumped = await db.bump_unread_for_group(
                conn, 1, self._members(), sender_id=5, online_ids={7})
        self.assertEqual(bumped, [6])
        async with db.connect(self.path) as conn:
            counts6 = await db.get_unread_counts(conn, 6)
            counts5 = await db.get_unread_counts(conn, 5)
            counts7 = await db.get_unread_counts(conn, 7)
            counts8 = await db.get_unread_counts(conn, 8)
        self.assertEqual(counts6, {1: 1})
        self.assertEqual(counts5, {})   # sender not bumped
        self.assertEqual(counts7, {})   # online not bumped
        self.assertEqual(counts8, {})   # bot not bumped


if __name__ == "__main__":
    unittest.main()
