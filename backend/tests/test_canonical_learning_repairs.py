from __future__ import annotations

import os
import tempfile
import unittest

import db

from memory.application import CanonicalLearningService
from memory.infrastructure import MemorySchemaManager
from memory.ports import MemoryDatabasePort


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int | None, *, write: bool = False):
        return db.connect(self.path)


class CanonicalLearningRepairTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_learning_repairs.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        async with db.connect(self.path) as conn:
            await conn.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY,group_id INTEGER,member_id INTEGER,content TEXT,is_deleted INTEGER DEFAULT 0)")
            await conn.commit()
        self.service = CanonicalLearningService(self.database)

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_observation_gap_is_repaired_in_canonical_jobs(self) -> None:
        async with db.connect(self.path) as conn:
            await conn.execute("INSERT INTO messages(id,group_id,member_id,content,is_deleted) VALUES(1,7,5,'reply',0)")
            await conn.commit()
        self.assertEqual(await self.service.repair_observation_gaps(7), 1)
        async with db.connect(self.path) as conn:
            async with conn.execute("SELECT job_type,input_id FROM pipeline_jobs") as cur:
                self.assertEqual(await cur.fetchall(), [("observe_turn", "1:5")])

    async def test_skill_projection_gap_is_repaired_in_canonical_jobs(self) -> None:
        async with db.connect(self.path) as conn:
            await conn.execute("INSERT INTO skills(skill_id,group_id,bot_id,name,maturity,risk_level,status,created_at,updated_at) VALUES('skill:1',7,5,'x','active','S0','active',1,1)")
            await conn.execute("INSERT INTO skill_versions(skill_id,version,declaration_json,content_hash,created_at) VALUES('skill:1',1,'{}','x',1)")
            await conn.commit()
        self.assertEqual(await self.service.repair_skill_projection_gaps(7), 1)
