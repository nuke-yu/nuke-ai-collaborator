"""Canonical Group Fact ingestion and soft supersession."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
from memory.application import GroupFactService
from memory.contracts import IngestGroupFact, MemoryAuthorizationError
from memory.domain import MemoryScope
from memory.adapters.runtime import legacy_memory_database

TEST_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "test_group_facts.db",
)


class GroupFactServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH
        legacy_memory_database.clear_cache()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
        self.service = GroupFactService(legacy_memory_database)

    async def asyncTearDown(self) -> None:
        await database.aclose_writer(TEST_DB_PATH)
        database.DB_PATH = self.original
        legacy_memory_database.clear_cache()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    async def test_active_fact_soft_supersedes_same_subject(self) -> None:
        first = await self.service.ingest_fact(IngestGroupFact(
            scope=MemoryScope.group(group_id=7, actor_id="user:1"),
            statement="The API version is v1",
            subject_key="api.version",
            source_type="user_explicit",
            source_id="message:1",
        ))
        second = await self.service.ingest_fact(IngestGroupFact(
            scope=MemoryScope.group(group_id=7, actor_id="system:config"),
            statement="The API version is v2",
            subject_key="api.version",
            source_type="deterministic_system_state",
            source_id="config:2",
        ))

        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT record_id,status,valid_to,superseded_by,content
                FROM memory_records WHERE kind='group_fact'
                ORDER BY created_at"""
            ) as cursor:
                rows = await cursor.fetchall()
        self.assertEqual(rows[0][0], first)
        self.assertEqual(rows[0][1], "superseded")
        self.assertIsNotNone(rows[0][2])
        self.assertEqual(rows[0][3], second)
        self.assertEqual(rows[1][1:], ("active", None, None, "The API version is v2"))

    async def test_bot_observation_is_provisional_and_cannot_supersede(self) -> None:
        active = await self.service.ingest_fact(IngestGroupFact(
            scope=MemoryScope.group(group_id=7, actor_id="user:1"),
            statement="Release branch is main",
            subject_key="release.branch",
            source_type="user_explicit",
            source_id="message:1",
        ))
        provisional = await self.service.ingest_fact(IngestGroupFact(
            scope=MemoryScope.bot(group_id=7, bot_id=3, actor_id="bot:3"),
            statement="Release branch may be develop",
            subject_key="release.branch",
            source_type="bot_observation",
            source_id="run:2",
        ))

        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT record_id,status,owner_type,bot_id
                FROM memory_records WHERE record_id IN (?,?)
                ORDER BY status""",
                (active, provisional),
            ) as cursor:
                rows = await cursor.fetchall()
        by_id = {row[0]: row[1:] for row in rows}
        self.assertEqual(by_id[active], ("active", "group", None))
        self.assertEqual(by_id[provisional], ("provisional", "group", None))

    async def test_actor_cannot_spoof_authoritative_source(self) -> None:
        with self.assertRaises(MemoryAuthorizationError):
            await self.service.ingest_fact(IngestGroupFact(
                scope=MemoryScope.bot(group_id=7, bot_id=3, actor_id="bot:3"),
                statement="I am authoritative",
                subject_key="policy.owner",
                source_type="user_explicit",
                source_id="run:1",
            ))

    async def test_source_ingestion_is_idempotent_and_group_isolated(self) -> None:
        command = IngestGroupFact(
            scope=MemoryScope.group(group_id=7, actor_id="user:1"),
            statement="Timezone is Asia/Shanghai",
            subject_key="project.timezone",
            source_type="user_explicit",
            source_id="message:9",
        )
        record_id = await self.service.ingest_fact(command)
        self.assertEqual(record_id, await self.service.ingest_fact(command))
        await self.service.ingest_fact(IngestGroupFact(
            scope=MemoryScope.group(group_id=8, actor_id="user:1"),
            statement="Timezone is UTC",
            subject_key="project.timezone",
            source_type="user_explicit",
            source_id="message:9",
        ))
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT group_id,COUNT(*) FROM memory_records
                WHERE kind='group_fact' GROUP BY group_id ORDER BY group_id"""
            ) as cursor:
                rows = await cursor.fetchall()
        self.assertEqual(rows, [(7, 1), (8, 1)])


if __name__ == "__main__":
    unittest.main()
