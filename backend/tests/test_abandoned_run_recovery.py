"""Unit tests for abandoned execution run recovery."""
import os
import tempfile
import time
import unittest

import db
from ai.execution_runs import finish_run, recover_abandoned_runs, start_run
from memory.adapters.runtime import legacy_memory_database
from memory.infrastructure.schema import MemorySchemaManager
from memory.ports import MemoryDatabasePort


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int, *, write: bool = False):
        return db.connect(self.path)


class AbandonedRunRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_abandoned_runs.db")
        self.original_path = db.DB_PATH
        db.DB_PATH = self.path
        legacy_memory_database.clear_cache()
        await db.init_db()
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)

    async def asyncTearDown(self) -> None:
        await db.aclose_writer(self.path)
        db.DB_PATH = self.original_path
        legacy_memory_database.clear_cache()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_finish_run_accepts_abandoned_status(self) -> None:
        await start_run(
            run_id="run-1",
            group_id=7,
            bot_id=3,
            session_id="sess-1",
            thread_id="t-1",
            provider="openai",
            model="gpt-test",
            executor="tool_loop_v1",
        )
        await finish_run(
            run_id="run-1",
            group_id=7,
            status="abandoned",
            error_summary="worker_process_crashed",
        )

        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT status, error_summary FROM agent_runs WHERE run_id='run-1'"
            ) as cursor:
                row = await cursor.fetchone()

        self.assertEqual(row[0], "abandoned")
        self.assertEqual(row[1], "worker_process_crashed")

    async def test_recover_abandoned_runs_recovers_stale_runs(self) -> None:
        await start_run(
            run_id="stale-run",
            group_id=7,
            bot_id=3,
            session_id="sess-2",
            thread_id="t-2",
            provider="openai",
            model="gpt-test",
            executor="tool_loop_v1",
        )

        # Backdate updated_at to simulate a crash 10 minutes ago
        past_time = int((time.time() - 600) * 1000)
        async with db.connect(self.path) as connection:
            await connection.execute(
                "UPDATE agent_runs SET updated_at=? WHERE run_id='stale-run'",
                (past_time,),
            )
            await connection.commit()

        recovered_count = await recover_abandoned_runs(7, timeout_seconds=300)
        self.assertEqual(recovered_count, 1)

        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT status, error_summary FROM agent_runs WHERE run_id='stale-run'"
            ) as cursor:
                row = await cursor.fetchone()

        self.assertEqual(row[0], "abandoned")
        self.assertIn("abandoned_stale_worker_timeout", row[1])


if __name__ == "__main__":
    unittest.main()
