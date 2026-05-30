"""
tests/test_db_writer.py — DFT-053 串行写入器消除 database is locked

每次查询各开一条 aiosqlite 连接（各跑独立 OS 线程），多个写连接并发争抢
SQLite 单写锁 → "database is locked"。db.writer 提供进程级单写连接 + 串行锁：
所有写经同一连接同一把锁，写事务永不重叠，SQLite 不再见到并发写者。
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import writer as writer_mod


class TestSerialWriter(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._orig_path = writer_mod.DB_PATH
        writer_mod.DB_PATH = self._tmp.name
        async with writer_mod.write_connect() as conn:
            await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
            await conn.commit()

    async def asyncTearDown(self):
        await writer_mod.aclose_writer()
        writer_mod.DB_PATH = self._orig_path
        os.unlink(self._tmp.name)

    async def test_write_and_read_back(self):
        async with writer_mod.write_connect() as conn:
            await conn.execute("INSERT INTO t (v) VALUES (?)", (42,))
            await conn.commit()
        async with writer_mod.write_connect() as conn:
            async with conn.execute("SELECT v FROM t") as cur:
                row = await cur.fetchone()
        self.assertEqual(row[0], 42)

    async def test_shares_single_connection(self):
        async with writer_mod.write_connect() as c1:
            first = c1
        async with writer_mod.write_connect() as c2:
            second = c2
        self.assertIs(first, second)

    async def test_serializes_concurrent_writers(self):
        order = []

        async def w(n):
            async with writer_mod.write_connect() as conn:
                order.append(f"start{n}")
                await asyncio.sleep(0.05)  # hold the writer; a non-serial impl would interleave
                await conn.execute("INSERT INTO t (v) VALUES (?)", (n,))
                await conn.commit()
                order.append(f"end{n}")

        await asyncio.gather(w(1), w(2))

        # serialized → each writer's start/end are adjacent, never interleaved
        self.assertIn(order, [["start1", "end1", "start2", "end2"],
                              ["start2", "end2", "start1", "end1"]])
        # and both writes landed without "database is locked"
        async with writer_mod.write_connect() as conn:
            async with conn.execute("SELECT COUNT(*) FROM t") as cur:
                (cnt,) = await cur.fetchone()
        self.assertEqual(cnt, 2)

    async def test_aclose_resets_connection(self):
        async with writer_mod.write_connect() as c1:
            first = c1
        await writer_mod.aclose_writer()
        async with writer_mod.write_connect() as c2:
            second = c2
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
