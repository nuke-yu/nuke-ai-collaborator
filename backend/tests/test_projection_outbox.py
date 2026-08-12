from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import db as database
from ai.cases import assemble_case
from ai.experiences import distill_case, reconcile_experience_projections
from ai.projection_outbox import drain_projection_outbox, enqueue_projection

TEST_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "test_projection_outbox.db",
)


class ProjectionOutboxTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._original_db_path = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()

    async def asyncTearDown(self) -> None:
        await database.aclose_writer(TEST_DB_PATH)
        database.DB_PATH = self._original_db_path
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    async def _corrected_case(self, run_id: str = "outbox") -> str:
        return await assemble_case(
            run_id=run_id,
            group_id=7,
            bot_id=3,
            task="repair durable projection",
            outcome="completed",
            tool_records=[
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest tests/test_projection_outbox.py"},
                    "result": "1 failed",
                    "is_error": True,
                },
                {
                    "name": "edit_file",
                    "args": {"path": "backend/ai/experiences.py"},
                    "result": "edited",
                    "is_error": False,
                },
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest tests/test_projection_outbox.py"},
                    "result": "1 passed",
                    "is_error": False,
                },
            ],
        )

    async def test_projection_failure_keeps_committed_retryable_intent(self) -> None:
        case_id = await self._corrected_case()
        with patch(
            "memory.adapters.projections.chroma_client.ChromaProjectionClient.write_sync",
            side_effect=RuntimeError("chroma unavailable"),
        ):
            record_id = await distill_case(case_id, 7)

        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT content FROM memory_records WHERE record_id=?", (record_id,)
            ) as cur:
                self.assertIsNotNone(await cur.fetchone())
            async with db.execute(
                "SELECT status,attempt_count,last_error FROM memory_projection_outbox"
            ) as cur:
                status, attempts, error = await cur.fetchone()
        self.assertEqual((status, attempts), ("pending", 1))
        self.assertIn("RuntimeError", error)

        async with database.write_connect(TEST_DB_PATH) as db:
            await db.execute(
                "UPDATE memory_projection_outbox SET next_attempt_at=0"
            )
            await db.commit()
        with patch(
            "memory.adapters.projections.chroma_client.ChromaProjectionClient.write_sync"
        ) as index_vector:
            result = await drain_projection_outbox(7)

        self.assertEqual((result.claimed, result.completed, result.failed), (1, 1, 0))
        index_vector.assert_called_once()
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT status,completed_at FROM memory_projection_outbox"
            ) as cur:
                status, completed_at = await cur.fetchone()
        self.assertEqual(status, "completed")
        self.assertIsNotNone(completed_at)

    async def test_hydration_reconciliation_replays_canonical_projection(self) -> None:
        case_id = await self._corrected_case("reconcile")
        with patch(
            "memory.adapters.projections.chroma_client.ChromaProjectionClient.write_sync"
        ):
            await distill_case(case_id, 7)

        self.assertEqual(await reconcile_experience_projections(7), 1)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT status FROM memory_projection_outbox"
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], "pending")

        with patch(
            "memory.adapters.projections.chroma_client.ChromaProjectionClient.write_sync"
        ) as index_vector:
            result = await drain_projection_outbox(7)
        self.assertEqual(result.completed, 1)
        index_vector.assert_called_once()

    async def test_superseded_inflight_delivery_cannot_ack_latest_version(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_delivery(projection_type, payload):
            started.set()
            await release.wait()

        async with database.write_connect(TEST_DB_PATH) as db:
            await enqueue_projection(
                db,
                event_id="experience-vector:exp:1",
                projection_type="experience_vector_upsert",
                aggregate_id="exp:1",
                aggregate_version="v1",
                group_id=7,
                payload={"content": "old"},
            )
            await db.commit()

        with patch(
            "memory.adapters.projections.chroma.ChromaBotMemoryProjectionDelivery.deliver",
            side_effect=delayed_delivery,
        ):
            first_drain = asyncio.create_task(drain_projection_outbox(7))
            await started.wait()
            async with database.write_connect(TEST_DB_PATH) as db:
                await enqueue_projection(
                    db,
                    event_id="experience-vector:exp:1",
                    projection_type="experience_vector_upsert",
                    aggregate_id="exp:1",
                    aggregate_version="v2",
                    group_id=7,
                    payload={"content": "latest"},
                )
                await db.commit()
            release.set()
            first_result = await first_drain

        self.assertEqual(first_result.completed, 0)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT aggregate_version,status FROM memory_projection_outbox"
            ) as cur:
                self.assertEqual(await cur.fetchone(), ("v2", "pending"))


if __name__ == "__main__":
    unittest.main()
