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
        self.assertIn("agent_case_attempts", tables)
        self.assertIn("memory_relations", tables)
        async with db.connect(self.path) as connection:
            async with connection.execute(
                "PRAGMA table_info(agent_cases)"
            ) as cursor:
                case_columns = {row[1] for row in await cursor.fetchall()}
            async with connection.execute(
                "PRAGMA table_info(memory_records)"
            ) as cursor:
                record_columns = {row[1] for row in await cursor.fetchall()}
            async with connection.execute(
                "PRAGMA table_info(memory_relations)"
            ) as cursor:
                relation_columns = {row[1] for row in await cursor.fetchall()}
            async with connection.execute(
                "PRAGMA table_info(memory_projection_rollout)"
            ) as cursor:
                rollout_columns = {row[1] for row in await cursor.fetchall()}
        self.assertTrue(
            {"semantic_cluster_key", "task_family", "task_concepts_json"}
            <= case_columns
        )
        self.assertTrue(
            {
                "semantic_cluster_key",
                "environment_signature",
                "failure_signature",
            }
            <= record_columns
        )
        self.assertTrue(
            {"qualified_since", "cooldown_until"} <= rollout_columns
        )
        self.assertTrue(
            {
                "relation_id",
                "group_id",
                "from_record_id",
                "to_record_id",
                "relation_type",
                "source_type",
                "source_id",
                "evidence_json",
                "effective_from",
                "valid_to",
            }
            <= relation_columns
        )
        self.assertTrue(
            {
                "owner_type",
                "authority",
                "subject_key",
                "sensitivity",
                "evidence_json",
                "created_by",
                "effective_from",
            }
            <= record_columns
        )

    async def test_v8_rollout_state_is_upgraded_for_hysteresis(self) -> None:
        async with db.connect(self.path) as connection:
            await connection.execute(
                """CREATE TABLE memory_schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            for version in range(1, 9):
                await connection.execute(
                    "INSERT INTO memory_schema_version(version) VALUES (?)",
                    (version,),
                )
            await connection.execute(
                """CREATE TABLE memory_projection_rollout (
                    group_id INTEGER PRIMARY KEY,
                    consecutive_passes INTEGER NOT NULL DEFAULT 0,
                    required_passes INTEGER NOT NULL DEFAULT 3,
                    direct_write_enabled INTEGER NOT NULL DEFAULT 1,
                    last_audit_passed INTEGER NOT NULL DEFAULT 0,
                    last_audited_at INTEGER NOT NULL DEFAULT 0,
                    last_failure_reason TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL DEFAULT 0
                )"""
            )
            await connection.execute(
                """INSERT INTO memory_projection_rollout
                (group_id,consecutive_passes,direct_write_enabled)
                VALUES (7,2,1)"""
            )
            await connection.commit()

        await self.schema.ensure_group(7)

        async with db.connect(self.path) as connection:
            async with connection.execute(
                """SELECT consecutive_passes,direct_write_enabled,
                    qualified_since,cooldown_until
                FROM memory_projection_rollout WHERE group_id=7"""
            ) as cursor:
                row = await cursor.fetchone()
        self.assertEqual(tuple(row), (2, 1, 0, 0))

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
                self.assertEqual(
                    (await cursor.fetchone())[0], MEMORY_SCHEMA_VERSION
                )

    async def test_v1_usage_tables_are_upgraded_without_losing_rows(self) -> None:
        async with db.connect(self.path) as connection:
            await connection.execute(
                """CREATE TABLE memory_schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            await connection.execute(
                "INSERT INTO memory_schema_version(version) VALUES (1)"
            )
            for statement in (
                """CREATE TABLE experience_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, group_id INTEGER NOT NULL, bot_id INTEGER,
                    state TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    tool_attempts INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    UNIQUE(record_id, run_id)
                )""",
                """CREATE TABLE skill_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL,
                    version INTEGER NOT NULL, run_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL, outcome TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'injected',
                    created_at INTEGER NOT NULL, UNIQUE(skill_id,run_id)
                )""",
            ):
                await connection.execute(statement)
            await connection.execute(
                """INSERT INTO experience_usage
                (record_id,run_id,group_id,state,created_at,updated_at)
                VALUES ('record:1','run:1',7,'injected',1,1)"""
            )
            await connection.execute(
                """INSERT INTO skill_usage
                (skill_id,version,run_id,group_id,created_at)
                VALUES ('skill:1',1,'run:1',7,1)"""
            )
            await connection.commit()

        await self.schema.ensure_group(7)

        async with db.connect(self.path) as connection:
            for table in ("experience_usage", "skill_usage"):
                async with connection.execute(f"PRAGMA table_info({table})") as cursor:
                    columns = {row[1] for row in await cursor.fetchall()}
                self.assertTrue(
                    {
                        "adopted_at",
                        "executed_at",
                        "verified_at",
                        "adopted_via",
                        "adoption_evidence_json",
                        "execution_evidence_json",
                        "verification_status",
                        "verification_evidence_json",
                    }
                    <= columns
                )
            async with connection.execute(
                "SELECT state,verification_status FROM experience_usage"
            ) as cursor:
                self.assertEqual(await cursor.fetchone(), ("injected", "unverified"))
            async with connection.execute(
                "SELECT state,verification_status FROM skill_usage"
            ) as cursor:
                self.assertEqual(await cursor.fetchone(), ("injected", "unverified"))
            async with connection.execute(
                "PRAGMA table_info(agent_cases)"
            ) as cursor:
                case_columns = {row[1] for row in await cursor.fetchall()}
            self.assertTrue(
                {
                    "outcome_status",
                    "verification_adapter",
                    "correction_evidence_json",
                }
                <= case_columns
            )

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
