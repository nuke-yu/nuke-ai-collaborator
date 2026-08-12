from __future__ import annotations

import os
import tempfile
import unittest

import db

from memory.application import CanonicalConversationMemoryService
from memory.contracts import ForgetMemory, ObserveMemory, RecallMemory
from memory.domain import MemoryScope
from memory.infrastructure import MemorySchemaManager
from memory.ports import MemoryDatabasePort


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int | None, *, write: bool = False):
        return db.connect(self.path)


class CanonicalConversationMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_conversation.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.service = CanonicalConversationMemoryService(self.database)
        self.scope = MemoryScope.bot(group_id=7, bot_id=5, actor_id="bot:5")

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_observe_and_recall_use_canonical_records(self) -> None:
        await self.service.observe(
            ObserveMemory(
                scope=self.scope,
                source_id="message:1",
                content="React 19 is the group frontend standard",
                metadata={"source_type": "conversation"},
            )
        )

        result = await self.service.recall(
            RecallMemory(scope=self.scope, query="React 19")
        )

        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].kind, "conversation")
        self.assertIn("React 19", result.rendered_context)
        self.assertIn("nuke.canonical.conversation", result.algorithm_trace[0]["algorithm_id"])

    async def test_group_and_bot_scope_are_hard_storage_boundaries(self) -> None:
        await self.service.observe(
            ObserveMemory(scope=self.scope, source_id="message:1", content="private bot memory")
        )
        other_bot = MemoryScope.bot(group_id=7, bot_id=6, actor_id="bot:6")
        result = await self.service.recall(
            RecallMemory(scope=other_bot, query="private bot memory")
        )
        self.assertEqual(result.hits, ())

    async def test_forget_only_deletes_current_bot_conversation_records(self) -> None:
        await self.service.observe(
            ObserveMemory(scope=self.scope, source_id="message:1", content="remove me")
        )
        await self.service.forget(ForgetMemory(scope=self.scope))
        result = await self.service.recall(
            RecallMemory(scope=self.scope, query="remove me")
        )
        self.assertEqual(result.hits, ())
