"""Tests for the schema lifecycle owned by the Memory module."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import AbstractAsyncContextManager
from typing import Any

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite
import db

from memory.contracts import MemoryOperationError
from memory.infrastructure.schema import (
    MEMORY_GROUP_TABLES,
    MEMORY_SCHEMA_VERSION,
    MemorySchemaManager,
)
from memory.ports import MemoryDatabasePort, MemorySchemaPort


class _PathDatabase:
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(
        self, table_name: str, group_id: int | None, *, write: bool
    ) -> AbstractAsyncContextManager[Any]:
        return db.connect(self.path)


class MemorySchemaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_memory.db")
        self.database = _PathDatabase(self.path)
        self.schema = MemorySchemaManager(self.database)

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_fresh_database_is_self_initialized_and_versioned(self) -> None:
        version = await self.schema.ensure_group(7)

        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cursor:
                tables = {row[0] for row in await cursor.fetchall()}
            async with connection.execute(
                "SELECT MAX(version) FROM memory_schema_version"
            ) as cursor:
                stored_version = (await cursor.fetchone())[0]

        self.assertEqual(version, MEMORY_SCHEMA_VERSION)
        self.assertEqual(stored_version, MEMORY_SCHEMA_VERSION)
        self.assertTrue(MEMORY_GROUP_TABLES <= tables)

    async def test_ensure_is_idempotent_and_preserves_canonical_records(self) -> None:
        await self.schema.ensure_group(7)
        async with db.connect(self.path) as connection:
            await connection.execute(
                """INSERT INTO memory_records
                (record_id,kind,group_id,content,created_at,updated_at)
                VALUES ('record:1','experience',7,'durable',1,1)"""
            )
            await connection.commit()

        await self.schema.ensure_group(7)

        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT content FROM memory_records WHERE record_id='record:1'"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], "durable")
            async with connection.execute(
                "SELECT COUNT(*) FROM memory_schema_version"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_audit_immutability_is_part_of_owned_schema(self) -> None:
        await self.schema.ensure_group(7)
        async with db.connect(self.path) as connection:
            await connection.execute(
                """INSERT INTO skill_promotion_audit
                (skill_id,group_id,actor_id,reason,from_maturity,to_maturity,created_at)
                VALUES ('skill:1',7,'user:1','verified','candidate','trusted',1)"""
            )
            await connection.commit()
            with self.assertRaises(aiosqlite.IntegrityError):
                await connection.execute(
                    "UPDATE skill_promotion_audit SET reason='tampered' WHERE id=1"
                )

    async def test_missing_projection_table_is_repaired(self) -> None:
        await self.schema.ensure_group(7)
        async with db.connect(self.path) as connection:
            await connection.execute("DROP TABLE memory_projection_outbox")
            await connection.commit()

        await self.schema.ensure_group(7)

        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='memory_projection_outbox'"
            ) as cursor:
                self.assertIsNotNone(await cursor.fetchone())

    async def test_newer_schema_version_fails_closed(self) -> None:
        await self.schema.ensure_group(7)
        async with db.connect(self.path) as connection:
            await connection.execute(
                "INSERT INTO memory_schema_version(version) VALUES (?)",
                (MEMORY_SCHEMA_VERSION + 1,),
            )
            await connection.commit()

        with self.assertRaisesRegex(MemoryOperationError, "newer"):
            await self.schema.ensure_group(7)

    def test_schema_ports_are_runtime_conformant(self) -> None:
        self.assertIsInstance(self.database, MemoryDatabasePort)
        self.assertIsInstance(self.schema, MemorySchemaPort)


if __name__ == "__main__":
    unittest.main()
