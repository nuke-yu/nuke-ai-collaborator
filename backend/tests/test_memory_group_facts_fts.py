"""Unit tests for Group Fact FTS5 indexing and candidate recall."""
import os
import tempfile
import unittest

from memory.contracts import IngestGroupFact, RecallGroupFacts
from memory.domain import MemoryScope
from memory.application.group_facts import GroupFactService
from memory.infrastructure.schema import MemorySchemaManager
from memory.ports import MemoryDatabasePort


import db


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int, *, write: bool = False):
        return db.connect(self.path)


class GroupFactFTSTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_group_facts_fts.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.service = GroupFactService(self.database)
        self.scope = MemoryScope.group(group_id=7, actor_id="user:alice")

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_recalls_fact_beyond_200_item_limit(self) -> None:
        # Ingest 250 facts. Fact #1 is the oldest but contains unique keyword 'quantum_qubit'
        await self.service.ingest_fact(
            IngestGroupFact(
                scope=self.scope,
                statement="Quantum computer uses quantum_qubit architecture",
                subject_key="quantum_qubit_arch",
                source_type="user_explicit",
                source_id="msg_1",
            )
        )
        for i in range(2, 250):
            await self.service.ingest_fact(
                IngestGroupFact(
                    scope=self.scope,
                    statement=f"General info statement number {i}",
                    subject_key=f"info_{i}",
                    source_type="user_explicit",
                    source_id=f"msg_{i}",
                )
            )

        # Recall facts specifically targeting 'quantum_qubit'
        result = await self.service.recall_facts(
            RecallGroupFacts(
                scope=self.scope,
                query="quantum_qubit",
                limit=5,
            )
        )

        self.assertGreater(len(result.hits), 0)
        self.assertEqual(result.hits[0].provenance["subject_key"], "quantum_qubit_arch")
        self.assertIn("quantum_qubit", result.hits[0].content)

    async def test_fts_excludes_superseded_facts(self) -> None:
        # Ingest fact v1
        await self.service.ingest_fact(
            IngestGroupFact(
                scope=self.scope,
                statement="Database host is db1.example.com",
                subject_key="db_host",
                source_type="user_explicit",
                source_id="msg_101",
            )
        )
        # Update fact v2 (supersedes v1)
        await self.service.ingest_fact(
            IngestGroupFact(
                scope=self.scope,
                statement="Database host is db2.example.com",
                subject_key="db_host",
                source_type="user_explicit",
                source_id="msg_102",
            )
        )

        result = await self.service.recall_facts(
            RecallGroupFacts(
                scope=self.scope,
                query="db_host",
                limit=10,
            )
        )

        self.assertEqual(len(result.hits), 1)
        self.assertIn("db2.example.com", result.hits[0].content)

    async def test_subject_exact_match_boosts_ranking(self) -> None:
        await self.service.ingest_fact(
            IngestGroupFact(
                scope=self.scope,
                statement="PostgreSQL database cluster config",
                subject_key="postgresql_config",
                source_type="user_explicit",
                source_id="msg_201",
            )
        )
        await self.service.ingest_fact(
            IngestGroupFact(
                scope=self.scope,
                statement="We use postgresql for analytical queries",
                subject_key="general_analytics",
                source_type="user_explicit",
                source_id="msg_202",
            )
        )

        result = await self.service.recall_facts(
            RecallGroupFacts(
                scope=self.scope,
                query="postgresql_config",
                limit=2,
            )
        )

        self.assertGreaterEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].provenance["subject_key"], "postgresql_config")


if __name__ == "__main__":
    unittest.main()
