"""Unit tests for resumable, paginated projection rebuilds."""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock

import db
from memory.contracts import ExtractedFactObservation, IngestBotFactObservations
from memory.domain import MemoryScope
from memory.application.bot_facts import BotFactObservationService
from memory.application.projection_rebuild import BotMemoryProjectionRebuildService
from memory.infrastructure import MemorySchemaManager, ProjectionOutbox
from memory.ports import MemoryDatabasePort


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int, *, write: bool = False):
        return db.connect(self.path)


class ProjectionRebuildTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_projection_rebuild.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.outbox = ProjectionOutbox(self.database, AsyncMock())
        self.fact_service = BotFactObservationService(self.database, self.outbox)
        self.rebuild_service = BotMemoryProjectionRebuildService(self.database, self.outbox)
        self.scope = MemoryScope.bot(group_id=7, bot_id=3, actor_id="bot:3")

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_paginated_and_resumable_rebuild_flow(self) -> None:
        # Ingest 5 facts
        for i in range(1, 6):
            await self.fact_service.ingest(
                IngestBotFactObservations(
                    scope=self.scope,
                    source_id=f"message:{100 + i}",
                    facts=(
                        ExtractedFactObservation(
                            content=f"Fact content {i}",
                            importance=0.8,
                            projection_id=f"fact_3_7_{100 + i}_0",
                        ),
                    ),
                    role="developer",
                )
            )

        # 1. Start rebuild
        status = await self.rebuild_service.start_rebuild(7, mode="full_rebuild")
        self.assertEqual(status.status, "running")
        self.assertEqual(status.total_records, 5)
        self.assertEqual(status.processed_records, 0)

        # 2. Step 1 (batch_size=2 with 0 time budget for single batch)
        step1 = await self.rebuild_service.step_rebuild(7, batch_size=2, time_budget_ms=0)
        self.assertEqual(step1.status, "running")
        self.assertEqual(step1.processed_records, 2)
        self.assertNotEqual(step1.cursor_record_id, "")

        # 3. Pause rebuild
        paused = await self.rebuild_service.pause_rebuild(7)
        self.assertEqual(paused.status, "paused")

        # 4. Resume rebuild
        resumed = await self.rebuild_service.resume_rebuild(7)
        self.assertEqual(resumed.status, "running")

        # 5. Step 2 (batch_size=10 - completes remaining items)
        final_step = await self.rebuild_service.step_rebuild(7, batch_size=10, time_budget_ms=5000)
        self.assertEqual(final_step.status, "completed")
        self.assertEqual(final_step.processed_records, 5)
        self.assertIsNotNone(final_step.completed_at)


if __name__ == "__main__":
    unittest.main()
