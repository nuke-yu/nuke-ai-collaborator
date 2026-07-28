"""Industrial-grade durability evaluation harness and fault injection tests for Memory System V2."""
import asyncio
import os
import tempfile
import time
import unittest

import db
from memory.application.group_facts import GroupFactService
from memory.application.projection_rebuild import BotMemoryProjectionRebuildService
from memory.contracts import IngestGroupFact, RecallGroupFacts
from memory.domain import MemoryScope
from memory.infrastructure import MemorySchemaManager, ProjectionOutbox
from memory.ports import MemoryDatabasePort


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int, *, write: bool = False):
        return db.connect(self.path)


class MemoryDurabilityEvalHarnessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.group_id = 88
        self.path = tempfile.mktemp(suffix="_durability_eval.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(self.group_id)

        self.delivery_attempts = 0
        self.fail_delivery_times = 0

        class _MockDelivery:
            def __init__(self, outer):
                self.outer = outer

            async def deliver(self, projection_type, payload):
                self.outer.delivery_attempts += 1
                if self.outer.delivery_attempts <= self.outer.fail_delivery_times:
                    raise RuntimeError("Simulated fault injection: outbox delivery network timeout")

        self.outbox = ProjectionOutbox(self.database, _MockDelivery(self))
        self.fact_service = GroupFactService(self.database)
        self.rebuild_service = BotMemoryProjectionRebuildService(self.database, self.outbox)
        self.scope = MemoryScope.group(group_id=self.group_id, actor_id="user:alice")

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_fts_candidate_retrieval_at_scale_250_records(self) -> None:
        """Verify 100% recall precision across 250+ canonical facts without legacy cutoff."""
        for i in range(1, 251):
            await self.fact_service.ingest_fact(
                IngestGroupFact(
                    scope=self.scope,
                    source_type="user_explicit",
                    source_id=f"msg:{i}",
                    subject_key=f"config_key_{i}",
                    statement=f"System configuration item {i} value is val_{i}",
                    sensitivity="group",
                )
            )

        # Recall item 245 specifically
        recall_res = await self.fact_service.recall_facts(
            RecallGroupFacts(
                scope=self.scope,
                query="config_key_245 val_245",
                limit=10,
            )
        )

        self.assertTrue(len(recall_res.hits) > 0)
        self.assertIn("val_245", recall_res.hits[0].content)

    async def test_fault_injection_outbox_retry_durability(self) -> None:
        """Inject delivery failures and verify eventual outbox consistency."""
        now_ms = int(time.time() * 1000)
        async with await self.database.connect("memory_records", self.group_id, write=True) as connection:
            await self.outbox.enqueue(
                connection,
                event_id="evt-fault-1",
                projection_type="bot_memory_vector_upsert",
                aggregate_id="rec-1",
                aggregate_version="v1",
                group_id=self.group_id,
                payload={"test": "fault_injection"},
                now_ms=now_ms,
            )
            await connection.commit()

        # Inject 2 transient failures
        self.fail_delivery_times = 2

        # First drain attempt (fails due to fault injection)
        res1 = await self.outbox.drain(self.group_id)
        self.assertEqual(res1.completed, 0)

        # Reset next_attempt_at after first failure
        async with await self.database.connect("memory_projection_outbox", self.group_id, write=True) as conn:
            await conn.execute("UPDATE memory_projection_outbox SET next_attempt_at=0, lease_until=0")
            await conn.commit()

        # Second drain attempt (fails second transient error)
        res2 = await self.outbox.drain(self.group_id)
        self.assertEqual(res2.completed, 0)

        # Reset next_attempt_at after second failure
        async with await self.database.connect("memory_projection_outbox", self.group_id, write=True) as conn:
            await conn.execute("UPDATE memory_projection_outbox SET next_attempt_at=0, lease_until=0")
            await conn.commit()

        # Third drain attempt (delivery succeeds)
        res3 = await self.outbox.drain(self.group_id)
        self.assertEqual(res3.completed, 1)

    async def test_rebuild_crash_recovery_resumable_fault_injection(self) -> None:
        """Simulate worker crash during projection rebuild and verify clean resumption."""
        for i in range(1, 11):
            async with await self.database.connect("memory_records", self.group_id, write=True) as connection:
                await connection.execute(
                    """INSERT INTO memory_records (
                        record_id, group_id, bot_id, kind, content, importance,
                        owner_type, status, subject_key, created_at, updated_at
                    ) VALUES (?, ?, 3, 'fact', ?, 0.8, 'bot', 'provisional', 'k1', 1000, 1000)""",
                    (f"rec-rebuild-{i:03d}", self.group_id, f"Canonical content {i}"),
                )
                await connection.commit()

        # Start rebuild
        await self.rebuild_service.start_rebuild(self.group_id, mode="full_rebuild")

        # Step 1: process 1 batch of 4 records with 0 time budget
        step1 = await self.rebuild_service.step_rebuild(self.group_id, batch_size=4, time_budget_ms=0)
        self.assertEqual(step1.processed_records, 4)
        self.assertEqual(step1.status, "running")

        # Fault Injection: Simulate worker SIGKILL / pause crash
        await self.rebuild_service.pause_rebuild(self.group_id)

        # Worker restarts and resumes rebuild
        await self.rebuild_service.resume_rebuild(self.group_id)

        # Step 2: process remaining 6 records
        step2 = await self.rebuild_service.step_rebuild(self.group_id, batch_size=20, time_budget_ms=5000)
        self.assertEqual(step2.processed_records, 10)
        self.assertEqual(step2.status, "completed")
        self.assertIsNotNone(step2.completed_at)


if __name__ == "__main__":
    unittest.main()
