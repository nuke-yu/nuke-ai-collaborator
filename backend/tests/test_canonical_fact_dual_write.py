"""Legacy Chroma Fact extraction mirrored into canonical SQLite records."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from ai import memory
from memory.application import BotFactObservationService
from memory.contracts import (
    ExtractedFactObservation,
    IngestBotFactObservations,
    MemoryAuthorizationError,
)
from memory.domain import MemoryScope
from memory.infrastructure import MemorySchemaManager


class _PathDatabase:
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(
        self, table_name: str, group_id: int | None, *, write: bool
    ) -> AbstractAsyncContextManager[Any]:
        return db.connect(self.path)


def _command(*, actor_id: str = "bot:3") -> IngestBotFactObservations:
    return IngestBotFactObservations(
        scope=MemoryScope.bot(
            group_id=7,
            bot_id=3,
            actor_id=actor_id,
            thread_id="discussion:9",
        ),
        source_id="message:42",
        facts=(
            ExtractedFactObservation(
                content="The API uses version 2",
                importance=0.8,
                projection_id="fact_3_7_42_0",
            ),
            ExtractedFactObservation(
                content="The release branch is main",
                importance=0.7,
                projection_id="fact_3_7_42_1",
            ),
        ),
        role="developer",
        provider="openai",
        model="gpt-test",
        thread_id="discussion:9",
        observed_at=123_000,
        legacy_conflict_ids=("fact_3_7_10_0", "fact_3_7_10_0"),
    )


class BotFactObservationServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_fact_mirror.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.service = BotFactObservationService(self.database)

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_bot_facts_are_provisional_owned_and_evidence_bearing(self) -> None:
        record_ids = await self.service.ingest(_command())

        async with db.connect(self.path) as connection:
            async with connection.execute(
                """SELECT record_id,kind,group_id,bot_id,status,owner_type,
                    authority,sensitivity,confidence,importance,source_ids,
                    evidence_json,metadata_json,effective_from
                FROM memory_records ORDER BY record_id"""
            ) as cursor:
                rows = await cursor.fetchall()

        self.assertEqual(len(record_ids), 2)
        self.assertEqual({row[0] for row in rows}, set(record_ids))
        for row in rows:
            self.assertEqual(
                row[1:9],
                ("fact", 7, 3, "provisional", "bot", "bot_observation", "group", 0.5),
            )
            self.assertIn(row[9], {0.7, 0.8})
            self.assertEqual(row[10], '["message:42"]')
            self.assertIn('"legacy_conflict_ids": ["fact_3_7_10_0"]', row[11])
            self.assertIn('"projection_state": "legacy_chroma_direct_write"', row[12])
            self.assertEqual(row[13], 123_000)

    async def test_source_replay_is_idempotent_and_does_not_reactivate(self) -> None:
        record_ids = await self.service.ingest(_command())
        async with db.connect(self.path) as connection:
            await connection.execute(
                """UPDATE memory_records SET status='rejected'
                WHERE record_id=?""",
                (record_ids[0],),
            )
            await connection.commit()

        self.assertEqual(await self.service.ingest(_command()), record_ids)

        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT COUNT(*) FROM memory_records"
            ) as cursor:
                count = (await cursor.fetchone())[0]
        self.assertEqual(count, 2)
        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT status FROM memory_records WHERE record_id=?",
                (record_ids[0],),
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], "rejected")

    async def test_same_source_identity_remains_group_isolated(self) -> None:
        group_seven = await self.service.ingest(_command())
        group_eight = await self.service.ingest(replace(
            _command(),
            scope=MemoryScope.bot(
                group_id=8,
                bot_id=3,
                actor_id="bot:3",
                thread_id="discussion:9",
            ),
        ))

        self.assertTrue(set(group_seven).isdisjoint(group_eight))
        async with db.connect(self.path) as connection:
            async with connection.execute(
                """SELECT group_id,COUNT(*) FROM memory_records
                GROUP BY group_id ORDER BY group_id"""
            ) as cursor:
                self.assertEqual(await cursor.fetchall(), [(7, 2), (8, 2)])

    async def test_actor_must_match_owning_bot(self) -> None:
        with self.assertRaisesRegex(MemoryAuthorizationError, "match"):
            await self.service.ingest(_command(actor_id="bot:4"))


class LegacyFactDualWriteTest(unittest.IsolatedAsyncioTestCase):
    async def test_add_to_chroma_reuses_extraction_for_canonical_write(self) -> None:
        mirror = AsyncMock()
        with (
            patch.object(
                memory.FactExtractor,
                "extract",
                new=AsyncMock(return_value=[("API is v2", 0.8)]),
            ),
            patch.object(
                memory.ConflictResolver,
                "resolve_batch",
                new=AsyncMock(return_value=["fact_3_7_10_0"]),
            ),
            patch(
                "memory.bootstrap.build_bot_fact_observation_client",
                return_value=mirror,
            ),
            patch.object(memory.ChromaStore, "write_fact_sync"),
            patch.object(memory.ChromaStore, "delete_ids_sync"),
            patch("random.random", return_value=1.0),
        ):
            await memory.add_to_chroma(
                message_id=42,
                content="The API is now v2",
                role="developer",
                bot_id=3,
                group_id=7,
                provider="openai",
                model="gpt-test",
                thread_id="discussion:9",
                timestamp=123.0,
            )

        mirror.ingest.assert_awaited_once()
        command = mirror.ingest.await_args.args[0]
        self.assertEqual(command.source_id, "message:42")
        self.assertEqual(command.scope.group_id, 7)
        self.assertEqual(command.scope.bot_id, 3)
        self.assertEqual(command.scope.actor_id, "bot:3")
        self.assertEqual(command.facts[0].content, "API is v2")
        self.assertEqual(command.facts[0].projection_id, "fact_3_7_42_0")
        self.assertEqual(command.legacy_conflict_ids, ("fact_3_7_10_0",))
        self.assertEqual(command.observed_at, 123_000)

    async def test_canonical_failure_does_not_block_legacy_chroma(self) -> None:
        mirror = AsyncMock()
        mirror.ingest.side_effect = RuntimeError("canonical unavailable")
        write = unittest.mock.MagicMock()
        with (
            patch.object(
                memory.FactExtractor,
                "extract",
                new=AsyncMock(return_value=[("API is v2", 0.8)]),
            ),
            patch.object(
                memory.ConflictResolver,
                "resolve_batch",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "memory.bootstrap.build_bot_fact_observation_client",
                return_value=mirror,
            ),
            patch.object(memory.ChromaStore, "write_fact_sync", write),
            patch("random.random", return_value=1.0),
        ):
            with self.assertLogs("ai.memory", level="ERROR") as logs:
                await memory.add_to_chroma(
                    message_id=42,
                    content="The API is now v2",
                    role="developer",
                    bot_id=3,
                    group_id=7,
                )

        write.assert_called_once()
        self.assertTrue(
            any("canonical fact dual-write failed" in line for line in logs.output)
        )

    async def test_canonical_copy_is_secret_redacted(self) -> None:
        mirror = AsyncMock()
        token = "ghp_" + "a" * 36
        with (
            patch.object(
                memory.FactExtractor,
                "extract",
                new=AsyncMock(return_value=[(f"token={token}", 0.8)]),
            ),
            patch.object(
                memory.ConflictResolver,
                "resolve_batch",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "memory.bootstrap.build_bot_fact_observation_client",
                return_value=mirror,
            ),
            patch.object(memory.ChromaStore, "write_fact_sync"),
            patch("random.random", return_value=1.0),
        ):
            await memory.add_to_chroma(
                message_id=42,
                content="A sufficiently long token-bearing response",
                role="developer",
                bot_id=3,
                group_id=7,
            )

        canonical_content = mirror.ingest.await_args.args[0].facts[0].content
        self.assertNotIn(token, canonical_content)
        self.assertIn("[REDACTED", canonical_content)

    async def test_unscoped_legacy_fact_does_not_enter_canonical_store(self) -> None:
        builder = unittest.mock.MagicMock()
        with (
            patch.object(
                memory.FactExtractor,
                "extract",
                new=AsyncMock(return_value=[("legacy fact", 0.5)]),
            ),
            patch.object(
                memory.ConflictResolver,
                "resolve_batch",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "memory.bootstrap.build_bot_fact_observation_client",
                builder,
            ),
            patch.object(memory.ChromaStore, "write_fact_sync"),
            patch("random.random", return_value=1.0),
        ):
            await memory.add_to_chroma(
                message_id=42,
                content="A sufficiently long legacy fact",
                role="developer",
                bot_id=3,
                group_id=None,
            )

        builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
