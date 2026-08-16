from __future__ import annotations

import os
import tempfile
import unittest

import db

from memory.application.letta_controller import LettaMemoryFunctionController, LettaWorkingMemory
from memory.domain import MemoryScope
from memory.infrastructure import MemorySchemaManager


class _Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, *_args, **_kwargs):
        return db.connect(self.path)


class _ACL:
    async def check_acl(self, *_args, **_kwargs):
        return type("Decision", (), {"allowed": True, "reason": "ok"})()


class _Outbox:
    async def enqueue(self, *_args, **_kwargs):
        return None


class LettaControllerTest(unittest.IsolatedAsyncioTestCase):
    async def test_active_memory_functions_use_canonical_records_and_acl(self):
        path = tempfile.mktemp(suffix="_letta_controller.db")
        try:
            database = _Database(path)
            await MemorySchemaManager(database).ensure_group(7)
            controller = LettaMemoryFunctionController(database, _Outbox(), _ACL())
            scope = MemoryScope.bot(group_id=7, bot_id=3, actor_id="bot:3")
            written = await controller.execute(scope, "memory_write", {
                "content": "Use the deployment checklist",
                "source_id": "run:1",
                "importance": 0.9,
            })
            self.assertTrue(written.record_ids)
            read = await controller.execute(scope, "memory_read", {"query": "deployment"})
            self.assertEqual(read.records[0]["content"], "Use the deployment checklist")
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass

    async def test_dispatch_keeps_memory_functions_out_of_generic_tool_registry(self):
        from executors.tool_dispatch import dispatch_tool

        class _Result:
            operation = "memory_read"
            records = ({"content": "deployment"},)
            record_ids = ()

        class _Controller:
            async def execute(self, *_args, **_kwargs):
                return _Result()

        working = LettaWorkingMemory()
        result, is_error = await dispatch_tool(
            "memory_read", {"query": "deployment"},
            {"_memory_functions": _Controller(), "_memory_scope": MemoryScope.bot(
                group_id=7, bot_id=3, actor_id="bot:3"
            ), "_working_memory": working},
        )
        self.assertFalse(is_error)
        self.assertIn("deployment", result)


def test_working_memory_paging_is_local_and_bounded():
    working = LettaWorkingMemory()
    working.write("low", importance=0.1)
    working.write("high priority deployment note", importance=1.0)
    selected = working.page(8)
    assert selected[0]["content"] == "high priority deployment note"
    assert len(working.records) == 2
