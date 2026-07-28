"""Legacy Chroma reflections mirrored into canonical SQLite records."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from ai import memory
from memory.application import BotReflectionService
from memory.contracts import (
    IngestBotReflections,
    MemoryAuthorizationError,
    SynthesizedReflection,
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


def _command(*, actor_id: str = "bot:3") -> IngestBotReflections:
    return IngestBotReflections(
        scope=MemoryScope.bot(
            group_id=7,
            bot_id=3,
            actor_id=actor_id,
            thread_id="discussion:9",
        ),
        reflections=(
            SynthesizedReflection(
                content="Repeated deploy failures point to configuration drift",
                importance=0.9,
                projection_id="refl_3_7_123001_0_0",
                source_projection_ids=(
                    "fact_3_7_40_0",
                    "fact_3_7_41_0",
                    "fact_3_7_40_0",
                ),
                level=1,
                observed_at=123_001,
            ),
        ),
        role="developer",
        provider="openai",
        model="gpt-test",
        thread_id="discussion:9",
    )


class BotReflectionServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_reflection_mirror.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.service = BotReflectionService(self.database)

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_reflection_is_provisional_bot_inference_with_provenance(self) -> None:
        record_ids = await self.service.ingest(_command())

        async with db.connect(self.path) as connection:
            async with connection.execute(
                """SELECT record_id,kind,group_id,bot_id,status,owner_type,
                    authority,sensitivity,confidence,importance,source_ids,
                    evidence_json,metadata_json,effective_from
                FROM memory_records"""
            ) as cursor:
                row = await cursor.fetchone()

        self.assertEqual(row[0], record_ids[0])
        self.assertEqual(
            row[1:9],
            (
                "reflection",
                7,
                3,
                "provisional",
                "bot",
                "bot_inference",
                "group",
                0.4,
            ),
        )
        self.assertEqual(row[9], 0.9)
        self.assertEqual(
            row[10],
            '["fact_3_7_40_0", "fact_3_7_41_0"]',
        )
        self.assertIn(
            '"legacy_projection_id": "refl_3_7_123001_0_0"',
            row[11],
        )
        self.assertIn('"level": 1', row[12])
        self.assertEqual(row[13], 123_001)

    async def test_replay_is_idempotent_and_does_not_reactivate(self) -> None:
        record_ids = await self.service.ingest(_command())
        async with db.connect(self.path) as connection:
            await connection.execute(
                "UPDATE memory_records SET status='rejected' WHERE record_id=?",
                (record_ids[0],),
            )
            await connection.commit()

        self.assertEqual(await self.service.ingest(_command()), record_ids)

        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT COUNT(*),MIN(status) FROM memory_records"
            ) as cursor:
                self.assertEqual(await cursor.fetchone(), (1, "rejected"))

    async def test_same_projection_identity_remains_group_isolated(self) -> None:
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

        self.assertNotEqual(group_seven, group_eight)
        async with db.connect(self.path) as connection:
            async with connection.execute(
                """SELECT group_id,COUNT(*) FROM memory_records
                GROUP BY group_id ORDER BY group_id"""
            ) as cursor:
                self.assertEqual(await cursor.fetchall(), [(7, 1), (8, 1)])

    async def test_actor_must_match_owning_bot(self) -> None:
        with self.assertRaisesRegex(MemoryAuthorizationError, "match"):
            await self.service.ingest(_command(actor_id="bot:4"))


class LegacyReflectionDualWriteTest(unittest.IsolatedAsyncioTestCase):
    def _unconsolidated(self) -> dict:
        now = time.time()
        return {
            "ids": [f"fact_3_7_{index}_0" for index in range(5)],
            "documents": [f"deploy failed due to config {index}" for index in range(5)],
            "metadatas": [
                {
                    "mem_type": "fact",
                    "importance": 0.7,
                    "timestamp": now,
                    "thread_id": "discussion:9",
                }
                for _ in range(5)
            ],
        }

    async def test_maybe_reflect_reuses_synthesis_for_canonical_write(self) -> None:
        mirror = AsyncMock()
        legacy_write = MagicMock()
        with (
            patch.object(
                memory.ChromaStore,
                "get_unconsolidated_memories_sync",
                return_value=self._unconsolidated(),
            ),
            patch.object(
                memory.ConflictResolver,
                "resolve_batch",
                new=AsyncMock(return_value=[]),
            ),
            patch("ai.client.call_ai_once", new=AsyncMock(
                return_value={
                    "type": "text",
                    "content": "configuration drift causes repeated deploy failures|0.9",
                },
            )) as llm,
            patch(
                "memory.bootstrap.build_bot_reflection_client",
                return_value=mirror,
            ),
            patch.object(memory.ChromaStore, "write_fact_sync", legacy_write),
            patch.object(memory, "_get_all_reflection_watermarks", new=AsyncMock(return_value={})),
            patch.object(memory, "_set_reflection_watermark", new=AsyncMock()),
        ):
            await memory.maybe_reflect(
                group_id=7,
                bot_id=3,
                role="developer",
                provider="openai",
                model="gpt-test",
            )

        llm.assert_awaited_once()
        mirror.ingest.assert_awaited_once()
        command = mirror.ingest.await_args.args[0]
        reflection = command.reflections[0]
        self.assertEqual(
            reflection.content,
            "configuration drift causes repeated deploy failures",
        )
        self.assertEqual(reflection.importance, 0.9)
        self.assertEqual(reflection.level, 1)
        self.assertEqual(
            reflection.source_projection_ids,
            tuple(f"fact_3_7_{index}_0" for index in range(5)),
        )
        self.assertEqual(command.scope.actor_id, "bot:3")
        self.assertEqual(command.thread_id, "discussion:9")
        legacy_write.assert_called_once()
        self.assertEqual(
            legacy_write.call_args.args[0],
            reflection.projection_id,
        )

    async def test_canonical_failure_does_not_block_legacy_chroma(self) -> None:
        mirror = AsyncMock()
        mirror.ingest.side_effect = RuntimeError("canonical unavailable")
        legacy_write = MagicMock()
        with (
            patch.object(
                memory.ChromaStore,
                "get_unconsolidated_memories_sync",
                return_value=self._unconsolidated(),
            ),
            patch.object(
                memory.ConflictResolver,
                "resolve_batch",
                new=AsyncMock(return_value=[]),
            ),
            patch("ai.client.call_ai_once", new=AsyncMock(
                return_value={"type": "text", "content": "stable insight|0.8"},
            )),
            patch(
                "memory.bootstrap.build_bot_reflection_client",
                return_value=mirror,
            ),
            patch.object(memory.ChromaStore, "write_fact_sync", legacy_write),
            patch.object(memory, "_get_all_reflection_watermarks", new=AsyncMock(return_value={})),
            patch.object(memory, "_set_reflection_watermark", new=AsyncMock()),
        ):
            with self.assertLogs("ai.memory", level="ERROR") as logs:
                await memory.maybe_reflect(
                    group_id=7,
                    bot_id=3,
                    role="developer",
                )

        legacy_write.assert_called_once()
        self.assertTrue(any(
            "canonical reflection dual-write failed" in line
            for line in logs.output
        ))

    async def test_canonical_reflection_is_secret_redacted(self) -> None:
        token = "ghp_" + "a" * 36
        mirror = AsyncMock()
        with (
            patch.object(
                memory.ChromaStore,
                "get_unconsolidated_memories_sync",
                return_value=self._unconsolidated(),
            ),
            patch.object(
                memory.ConflictResolver,
                "resolve_batch",
                new=AsyncMock(return_value=[]),
            ),
            patch("ai.client.call_ai_once", new=AsyncMock(
                return_value={
                    "type": "text",
                    "content": f"deployment exposed token {token}|0.8",
                },
            )),
            patch(
                "memory.bootstrap.build_bot_reflection_client",
                return_value=mirror,
            ),
            patch.object(memory.ChromaStore, "write_fact_sync"),
            patch.object(memory, "_get_all_reflection_watermarks", new=AsyncMock(return_value={})),
            patch.object(memory, "_set_reflection_watermark", new=AsyncMock()),
        ):
            await memory.maybe_reflect(
                group_id=7,
                bot_id=3,
                role="developer",
            )

        content = mirror.ingest.await_args.args[0].reflections[0].content
        self.assertNotIn(token, content)
        self.assertIn("[REDACTED", content)


if __name__ == "__main__":
    unittest.main()
