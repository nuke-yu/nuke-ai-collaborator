from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import asynccontextmanager

import aiosqlite

from memory.application import CanonicalPersonalKnowledgeService
from memory.contracts import (
    CreatePersonalProjection,
    CreatePersonalRecord,
    FormatProjectedContext,
)
from memory.domain import MemoryScope
from memory.infrastructure import PersonalVaultDatabase


class _TempPersonalDatabase(PersonalVaultDatabase):
    def __init__(self, path: str) -> None:
        self.path = path

    @asynccontextmanager
    async def connect(self, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            for statement in self.__class__._ddl_for_test():
                await db.execute(statement)
            await db.commit()
            yield db

    @staticmethod
    def _ddl_for_test():
        # The production database owns the schema; this subclass only routes
        # the same service into an isolated test file.
        from memory.infrastructure.personal_database import _DDL
        return _DDL


class CanonicalPersonalMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_personal.db")
        self.database = _TempPersonalDatabase(self.path)
        self.service = CanonicalPersonalKnowledgeService(self.database)
        self.scope = MemoryScope.personal(user_id=7, actor_id="user:7", group_id=9)

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_record_projection_and_context_stay_in_canonical_vault(self) -> None:
        record_id = await self.service.create_record(CreatePersonalRecord(
            scope=self.scope, kind="preference", content="I prefer dark mode",
            source_type="manual", sensitivity="private",
        ))
        async with self.database.connect(7) as db:
            await db.execute("INSERT INTO personal_apps(app_id,user_id,name,status,created_at,updated_at) VALUES('chat',7,'Chat','active',1,1)")
            await db.commit()
        await self.service.create_projection(CreatePersonalProjection(
            scope=self.scope, record_id=record_id, target_group_id=9,
            purpose="assistant_context", app_id="chat",
        ))
        context = await self.service.format_projected_context(FormatProjectedContext(
            scope=self.scope, purpose="assistant_context", app_id="chat",
        ))
        self.assertIn("dark mode", context)

    async def test_secret_personal_record_cannot_be_projected(self) -> None:
        record_id = await self.service.create_record(CreatePersonalRecord(
            scope=self.scope, kind="profile", content="secret value",
            sensitivity="secret",
        ))
        with self.assertRaisesRegex(ValueError, "cannot be projected"):
            await self.service.create_projection(CreatePersonalProjection(
                scope=self.scope, record_id=record_id, target_group_id=9,
            ))
