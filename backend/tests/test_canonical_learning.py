from __future__ import annotations

import os
import tempfile
import unittest

import db

from memory.application import CanonicalLearningService
from memory.contracts import AssembleCase, RecallExperiences, RecallSkills
from memory.domain import MemoryScope
from memory.infrastructure import MemorySchemaManager
from memory.ports import MemoryDatabasePort


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int | None, *, write: bool = False):
        return db.connect(self.path)


class CanonicalLearningTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_learning.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.service = CanonicalLearningService(self.database)
        self.scope = MemoryScope.bot(group_id=7, bot_id=5, actor_id="bot:5")

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_recall_reads_canonical_experience_records(self) -> None:
        async with db.connect(self.path) as conn:
            await conn.execute("INSERT INTO memory_records(record_id,kind,group_id,bot_id,status,content,confidence,created_at,updated_at) VALUES('exp:1','experience',7,5,'active','SQLite migration succeeded',0.9,1,1)")
            await conn.commit()
        context, ids = await self.service.recall_experiences(RecallExperiences(scope=self.scope, query="SQLite migration", run_id="run:1"))
        self.assertEqual(ids, ["exp:1"])
        self.assertIn("SQLite migration", context)

    async def test_recall_skills_reads_canonical_skill_tables(self) -> None:
        async with db.connect(self.path) as conn:
            await conn.execute("INSERT INTO skills(skill_id,group_id,bot_id,name,maturity,risk_level,status,created_at,updated_at) VALUES('skill:1',7,5,'SQLite repair','active','S0','active',1,1)")
            await conn.execute("INSERT INTO skill_versions(skill_id,version,declaration_json,content_hash,created_at) VALUES('skill:1',1,'{\"trigger\":\"repair sqlite\",\"procedure\":[\"inspect schema\"]}','hash',1)")
            await conn.commit()
        context, ids = await self.service.recall_skills(RecallSkills(scope=self.scope, query="repair sqlite", run_id="run:1"))
        self.assertEqual(ids, ["skill:1"])
        self.assertIn("inspect schema", context)

    async def test_same_run_id_is_isolated_between_groups(self) -> None:
        first = await self.service.assemble_case(AssembleCase(
            scope=MemoryScope.group(group_id=1, actor_id="bot:1"),
            run_id="run:shared", task="group one task", outcome="completed",
        ))
        second = await self.service.assemble_case(AssembleCase(
            scope=MemoryScope.group(group_id=2, actor_id="bot:2"),
            run_id="run:shared", task="group two task", outcome="completed",
        ))

        self.assertNotEqual(first, second)
        async with db.connect(self.path) as conn:
            async with conn.execute(
                "SELECT group_id,run_id,case_id,task FROM agent_cases ORDER BY group_id"
            ) as cur:
                rows = await cur.fetchall()
        self.assertEqual([(row[0], row[1], row[3]) for row in rows], [
            (1, "run:shared", "group one task"),
            (2, "run:shared", "group two task"),
        ])
