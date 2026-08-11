"""Canonical relation persistence and tenant-boundary tests."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import AbstractAsyncContextManager
from typing import Any

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

from memory.application import CanonicalRelationService
from memory.contracts import (
    CreateMemoryRelation,
    MemoryOperationError,
    RecallMemoryRelations,
)
from memory.domain import MemoryRelationType, MemoryScope
from memory.infrastructure import MemorySchemaManager


class _PathDatabase:
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(
        self, table_name: str, group_id: int | None, *, write: bool
    ) -> AbstractAsyncContextManager[Any]:
        return db.connect(self.path)


class CanonicalRelationServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_memory_relations.db")
        self.database = _PathDatabase(self.path)
        self.schema = MemorySchemaManager(self.database)
        await self.schema.ensure_group(7)
        self.service = CanonicalRelationService(self.database)
        await self._insert_record("fact:old", 7)
        await self._insert_record("fact:new", 7)

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def _insert_record(self, record_id: str, group_id: int) -> None:
        async with db.connect(self.path) as connection:
            await connection.execute(
                """INSERT INTO memory_records
                (record_id,kind,group_id,content,created_at,updated_at)
                VALUES (?,'group_fact',?,'fact',1,1)""",
                (record_id, group_id),
            )
            await connection.commit()

    async def test_relation_is_idempotent_and_preserves_evidence(self) -> None:
        command = CreateMemoryRelation(
            scope=MemoryScope.group(group_id=7, actor_id="system:migration"),
            from_record_id="fact:new",
            to_record_id="fact:old",
            relation_type=MemoryRelationType.SUPERSEDES,
            source_type="canonical_transition",
            source_id="transition:1",
            evidence={"subject_key": "api.version"},
            effective_from=123,
        )

        relation_id = await self.service.create(command)
        self.assertEqual(relation_id, await self.service.create(command))
        relations = await self.service.recall(RecallMemoryRelations(
            scope=MemoryScope.bot(
                group_id=7, bot_id=3, actor_id="bot:3"
            ),
            record_id="fact:old",
        ))

        self.assertEqual(len(relations), 1)
        relation = relations[0]
        self.assertEqual(relation.relation_id, relation_id)
        self.assertEqual(relation.relation_type, MemoryRelationType.SUPERSEDES)
        self.assertEqual(relation.evidence, {"subject_key": "api.version"})
        self.assertEqual(relation.effective_from, 123)
        self.assertEqual(relation.created_by, "system:migration")

    async def test_relation_type_filter_is_explicit(self) -> None:
        for relation_type, source_id in (
            (MemoryRelationType.SUPPORTS, "evidence:1"),
            (MemoryRelationType.CONTRADICTS, "evidence:2"),
        ):
            await self.service.create(CreateMemoryRelation(
                scope=MemoryScope.group(group_id=7, actor_id="user:1"),
                from_record_id="fact:new",
                to_record_id="fact:old",
                relation_type=relation_type,
                source_type="message",
                source_id=source_id,
            ))

        relations = await self.service.recall(RecallMemoryRelations(
            scope=MemoryScope.group(group_id=7, actor_id="user:1"),
            record_id="fact:new",
            relation_types=(MemoryRelationType.CONTRADICTS,),
        ))

        self.assertEqual(
            [relation.relation_type for relation in relations],
            [MemoryRelationType.CONTRADICTS],
        )

    async def test_relation_recall_reconstructs_point_in_time_state(self) -> None:
        await self.service.create(CreateMemoryRelation(
            scope=MemoryScope.group(group_id=7, actor_id="system:timeline"),
            from_record_id="fact:new",
            to_record_id="fact:old",
            relation_type=MemoryRelationType.SUPERSEDES,
            source_type="timeline",
            source_id="timeline:1",
            effective_from=100,
        ))
        async with db.connect(self.path) as connection:
            await connection.execute(
                "UPDATE memory_relations SET valid_to=200"
            )
            await connection.commit()

        scope = MemoryScope.group(group_id=7, actor_id="system:timeline")
        self.assertEqual(
            len(await self.service.recall(RecallMemoryRelations(
                scope=scope, record_id="fact:new", as_of=150
            ))),
            1,
        )
        self.assertEqual(
            await self.service.recall(RecallMemoryRelations(
                scope=scope, record_id="fact:new", as_of=250
            )),
            (),
        )

    async def test_relation_recall_traverses_bounded_graph(self) -> None:
        await self._insert_record("fact:third", 7)
        await self.service.create(CreateMemoryRelation(
            scope=MemoryScope.group(group_id=7, actor_id="system:graph"),
            from_record_id="fact:new", to_record_id="fact:old",
            relation_type=MemoryRelationType.DERIVED_FROM,
            source_type="graph", source_id="edge:1", effective_from=100,
        ))
        await self.service.create(CreateMemoryRelation(
            scope=MemoryScope.group(group_id=7, actor_id="system:graph"),
            from_record_id="fact:old", to_record_id="fact:third",
            relation_type=MemoryRelationType.DERIVED_FROM,
            source_type="graph", source_id="edge:2", effective_from=100,
        ))
        relations = await self.service.recall(RecallMemoryRelations(
            scope=MemoryScope.group(group_id=7, actor_id="system:graph"),
            record_id="fact:new", max_hops=2,
        ))
        self.assertEqual(len(relations), 2)

    async def test_cross_group_endpoint_fails_closed(self) -> None:
        await self._insert_record("fact:other-group", 8)

        with self.assertRaisesRegex(MemoryOperationError, "requested group"):
            await self.service.create(CreateMemoryRelation(
                scope=MemoryScope.group(group_id=7, actor_id="user:1"),
                from_record_id="fact:new",
                to_record_id="fact:other-group",
                relation_type=MemoryRelationType.DERIVED_FROM,
                source_type="message",
                source_id="message:1",
            ))

        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT COUNT(*) FROM memory_relations"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 0)

    def test_contract_rejects_self_relation_and_missing_provenance(self) -> None:
        scope = MemoryScope.group(group_id=7, actor_id="user:1")
        with self.assertRaisesRegex(ValueError, "endpoints must differ"):
            CreateMemoryRelation(
                scope=scope,
                from_record_id="fact:old",
                to_record_id="fact:old",
                relation_type=MemoryRelationType.SUPPORTS,
                source_type="message",
                source_id="message:1",
            )
        with self.assertRaisesRegex(ValueError, "source_id"):
            CreateMemoryRelation(
                scope=scope,
                from_record_id="fact:new",
                to_record_id="fact:old",
                relation_type=MemoryRelationType.SUPPORTS,
                source_type="message",
                source_id="",
            )


if __name__ == "__main__":
    unittest.main()
