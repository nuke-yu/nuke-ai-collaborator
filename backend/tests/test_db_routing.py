"""CELL-04: contextvar DB routing.

A worker binds the "current group DB" for the duration of handling a group's
work; get_db()/write_connect() resolve to it without any call-site changes,
while global_db() always reaches the central DB. Binding is copied into child
tasks (bg.spawn) and resets on exit. Default (unbound) routes to DB_PATH, so
pre-split usage is unchanged.
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import db.writer as _writer
from db.context import bind_db, current_db_path


class TestDbRouting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.central = tempfile.mktemp(suffix="_central.db")
        self.group = tempfile.mktemp(suffix="_group.db")
        self._orig_db, self._orig_w = db.DB_PATH, _writer.DB_PATH
        db.DB_PATH = self.central
        _writer.DB_PATH = self.central

    async def asyncTearDown(self):
        await db.aclose_writer()
        db.DB_PATH, _writer.DB_PATH = self._orig_db, self._orig_w
        for p in (self.central, self.group):
            for s in ("", "-wal", "-shm"):
                try:
                    os.unlink(p + s)
                except FileNotFoundError:
                    pass

    async def test_unbound_defaults_to_db_path(self):
        self.assertIsNone(current_db_path.get())
        async with db.write_connect() as c:
            await c.execute("CREATE TABLE central_t (x)")
            await c.commit()
        # landed in central (the monkeypatched DB_PATH)
        async with db.connect(self.central) as c:
            cur = await c.execute("SELECT name FROM sqlite_master WHERE name='central_t'")
            self.assertIsNotNone(await cur.fetchone())

    async def test_bind_routes_reads_and_writes_to_group(self):
        with bind_db(self.group):
            async with db.write_connect() as c:
                await c.execute("CREATE TABLE g (x)")
                await c.execute("INSERT INTO g VALUES (1)")
                await c.commit()
            async with db.connect() as c:                 # read also routes to group
                cur = await c.execute("SELECT x FROM g")
                self.assertEqual((await cur.fetchone())[0], 1)
        # table g must be in the GROUP file, never the central one
        async with db.connect(self.central) as c:
            cur = await c.execute("SELECT name FROM sqlite_master WHERE name='g'")
            self.assertIsNone(await cur.fetchone())

    async def test_global_db_bypasses_binding(self):
        with bind_db(self.group):
            async with db.global_db() as c:               # central despite binding
                await c.execute("CREATE TABLE central_only (x)")
                await c.commit()
        async with db.connect(self.central) as c:
            cur = await c.execute("SELECT name FROM sqlite_master WHERE name='central_only'")
            self.assertIsNotNone(await cur.fetchone())

    async def test_binding_propagates_into_child_task(self):
        seen = {}

        async def child():
            seen["path"] = current_db_path.get()

        with bind_db(self.group):
            await asyncio.create_task(child())            # task copies context
        self.assertEqual(seen["path"], self.group)

    async def test_binding_resets_on_exit(self):
        with bind_db(self.group):
            self.assertEqual(current_db_path.get(), self.group)
        self.assertIsNone(current_db_path.get())


if __name__ == "__main__":
    unittest.main()
