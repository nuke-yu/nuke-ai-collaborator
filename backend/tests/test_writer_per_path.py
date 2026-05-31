"""CELL-03: db.writer parameterized per database path.

A worker owns N per-group private DBs; each must get its own serialized writer
(independent connection + independent lock) so writes to different group DBs
never contend, while writes to the SAME DB still serialize (DFT-053). Keyed by
(loop_id, db_path); `write_connect()`/`aclose_writer()` default to DB_PATH for
backward compatibility.
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.writer as writer


class TestPerPathWriter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.a = tempfile.mktemp(suffix="_a.db")
        self.b = tempfile.mktemp(suffix="_b.db")

    async def asyncTearDown(self):
        await writer.aclose_writer()  # close all writers for this loop
        for p in (self.a, self.b):
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(p + suffix)
                except FileNotFoundError:
                    pass

    async def test_distinct_paths_get_distinct_connections_and_data(self):
        async with writer.write_connect(self.a) as ca:
            await ca.execute("CREATE TABLE t (x INTEGER)")
            await ca.execute("INSERT INTO t VALUES (1)")
            await ca.commit()
            conn_a = ca

        async with writer.write_connect(self.b) as cb:
            self.assertIsNot(cb, conn_a)  # different DB → different connection
            cur = await cb.execute("SELECT name FROM sqlite_master WHERE name='t'")
            self.assertIsNone(await cur.fetchone())  # table t only exists in a

        async with writer.write_connect(self.a) as ca2:
            self.assertIs(ca2, conn_a)  # same DB → reused connection
            cur = await ca2.execute("SELECT x FROM t")
            self.assertEqual((await cur.fetchone())[0], 1)

    async def test_locks_are_independent_across_paths(self):
        order = []

        async def hold_a():
            async with writer.write_connect(self.a):
                order.append("a-in")
                await asyncio.sleep(0.2)
                order.append("a-out")

        async def use_b():
            await asyncio.sleep(0.05)  # ensure a is acquired first
            async with writer.write_connect(self.b):
                order.append("b-in")

        await asyncio.gather(hold_a(), use_b())
        # b must acquire while a is still held → different locks
        self.assertLess(order.index("b-in"), order.index("a-out"))

    async def test_same_path_serializes(self):
        order = []

        async def w(tag):
            async with writer.write_connect(self.a):
                order.append(f"{tag}-in")
                await asyncio.sleep(0.1)
                order.append(f"{tag}-out")

        await asyncio.gather(w("1"), w("2"))
        # one writer fully completes before the other starts (single lock per path)
        self.assertIn(order, (["1-in", "1-out", "2-in", "2-out"],
                              ["2-in", "2-out", "1-in", "1-out"]))

    async def test_aclose_single_path_leaves_others(self):
        async with writer.write_connect(self.a):
            pass
        async with writer.write_connect(self.b):
            pass
        lid = id(asyncio.get_running_loop())
        self.assertIn((lid, self.a), writer._state)
        self.assertIn((lid, self.b), writer._state)

        await writer.aclose_writer(self.a)
        self.assertNotIn((lid, self.a), writer._state)
        self.assertIn((lid, self.b), writer._state)   # b untouched

    async def test_default_path_backward_compatible(self):
        # no-arg write_connect() must still route to DB_PATH (monkeypatched here)
        writer.DB_PATH = self.a
        try:
            async with writer.write_connect() as c:
                await c.execute("CREATE TABLE u (y)")
                await c.commit()
            lid = id(asyncio.get_running_loop())
            self.assertIn((lid, self.a), writer._state)
        finally:
            writer.DB_PATH = os.path.join(os.path.dirname(writer.__file__), "chat.db")


if __name__ == "__main__":
    unittest.main()
