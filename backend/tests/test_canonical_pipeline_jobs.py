from __future__ import annotations

import os
import asyncio
import json
import tempfile
import unittest

import db

from memory.application.pipeline import (
    CanonicalPipelineDispatcher,
    RetryablePipelineJob,
)
from memory.domain import MemoryScope
from memory.infrastructure import MemorySchemaManager
from memory.infrastructure.pipeline_jobs import CanonicalPipelineJobRepository
from memory.ports import MemoryDatabasePort


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int | None, *, write: bool = False):
        return db.connect(self.path)


class CanonicalPipelineJobsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_pipeline.db")
        database = _PathDatabase(self.path)
        await MemorySchemaManager(database).ensure_group(7)
        self.repo = CanonicalPipelineJobRepository(database)
        self.scope = MemoryScope.group(group_id=7, actor_id="service:pipeline")

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_enqueue_is_idempotent_and_claim_is_fenced(self) -> None:
        first = await self.repo.enqueue(self.scope, "observe_turn", "message:1")
        second = await self.repo.enqueue(self.scope, "observe_turn", "message:1")
        self.assertEqual(first, second)

        ready = await self.repo.list_ready(self.scope)
        self.assertEqual([row["job_id"] for row in ready], [first])
        token = await self.repo.claim(self.scope, first)
        self.assertTrue(token and token.startswith("fence:"))
        terminal_state = {"job_id": first, "status": "completed"}
        self.assertFalse(await self.repo.complete_with_checkpoint(
            self.scope, first, "fence:stale", "{}", thread_id=first, state=terminal_state,
        ))
        self.assertTrue(await self.repo.complete_with_checkpoint(
            self.scope, first, token, "{}", thread_id=first, state=terminal_state,
        ))

    async def test_failed_job_is_retryable_and_stats_are_canonical(self) -> None:
        job_id = await self.repo.enqueue(self.scope, "project_skill", "skill:1")
        token = await self.repo.claim(self.scope, job_id)
        self.assertIsNotNone(token)
        self.assertTrue(await self.repo.fail(
            self.scope, job_id, token,
            "Authorization: Bearer canonical-test-token-1234567890",
        ))

        stats = await self.repo.stats(self.scope)
        self.assertEqual(stats["backlog"], 1)
        self.assertEqual(stats["retry"], 1)

        async with db.connect(self.path) as conn:
            async with conn.execute("SELECT error FROM pipeline_jobs WHERE job_id=?", (job_id,)) as cur:
                error = (await cur.fetchone())[0]
        self.assertNotIn("canonical-test-token-1234567890", error)

    async def test_dispatcher_uses_injected_handler_and_persists_checkpoints(self) -> None:
        job_id = await self.repo.enqueue(self.scope, "canonical_test", "input:1")

        async def handler(group_id: int, input_id: str, version: str):
            self.assertEqual((group_id, input_id, version), (7, "input:1", "1"))
            return {"ok": True}

        result = await CanonicalPipelineDispatcher(
            self.repo, {"canonical_test": handler}
        ).dispatch_group(7)
        if result["failed"]:
            async with db.connect(self.path) as conn:
                async with conn.execute("SELECT error FROM pipeline_jobs WHERE job_id=?", (job_id,)) as cur:
                    self.fail((await cur.fetchone())[0])
        self.assertEqual(result, {"claimed": 1, "completed": 1, "failed": 0})
        checkpoint = await self.repo.latest_checkpoint(self.scope, job_id)
        self.assertEqual(checkpoint["step_name"], "completed")

    async def test_deferred_handler_returns_job_without_consuming_retry(self) -> None:
        job_id = await self.repo.enqueue(self.scope, "deferred", "input:2")

        async def handler(*_args):
            raise RetryablePipelineJob("distillation required")

        result = await CanonicalPipelineDispatcher(
            self.repo, {"deferred": handler}
        ).dispatch_group(7)
        self.assertEqual(result["failed"], 0)
        async with db.connect(self.path) as conn:
            async with conn.execute(
                "SELECT status,attempt,lease_token FROM pipeline_jobs WHERE job_id=?",
                (job_id,),
            ) as cur:
                self.assertEqual(await cur.fetchone(), ("pending", 0, None))

    async def test_long_handler_renews_lease_and_runs_once(self) -> None:
        await self.repo.enqueue(self.scope, "slow", "input:slow")
        invocations = 0

        async def handler(*_args):
            nonlocal invocations
            invocations += 1
            await asyncio.sleep(1.5)
            return {"ok": True}

        result = await CanonicalPipelineDispatcher(
            self.repo, {"slow": handler}
        ).dispatch_group(7, lease_seconds=1)
        self.assertEqual(result, {"claimed": 1, "completed": 1, "failed": 0})
        self.assertEqual(invocations, 1)

    async def test_case_evaluation_can_enqueue_distill_job(self) -> None:
        from memory.application.case_evaluation import CanonicalCaseEvaluator

        async with db.connect(self.path) as conn:
            await conn.execute(
                """INSERT INTO agent_cases
                   (case_id,run_id,group_id,bot_id,task,outcome,outcome_status,
                    correction_evidence_json,created_at,updated_at)
                   VALUES ('case:distill','run:distill',7,5,'repair','completed',
                           'verified_success','{"fixed":true}',1,1)"""
            )
            await conn.commit()
        result = await CanonicalCaseEvaluator(self.repo._database).evaluate(
            7, "case:distill"
        )
        self.assertTrue(result["should_distill"])

    async def test_canonical_distiller_writes_experience_and_enqueues_skill_compile(self) -> None:
        from memory.application.experience_distillation import CanonicalExperienceDistiller

        async with db.connect(self.path) as conn:
            await conn.execute(
                """INSERT INTO agent_cases
                   (case_id,run_id,group_id,bot_id,task,task_signature,errors,
                    outcome,outcome_status,verification_adapter,correction_evidence_json,
                    created_at,updated_at)
                   VALUES ('case:exp','run:exp',7,5,'repair schema','sig',
                           '[\"failure\"]','completed','verified_success','pytest',
                           '{\"fixed\":true}',1,1)"""
            )
            await conn.commit()
        result = await CanonicalExperienceDistiller(self.repo._database).distill(7, "case:exp")
        self.assertTrue(result["distilled"])
        self.assertTrue(result["skill_job_id"])
        async with db.connect(self.path) as conn:
            await conn.execute(
                """INSERT INTO agent_cases
                   (case_id,run_id,group_id,bot_id,task,task_signature,errors,
                    outcome,outcome_status,verification_adapter,correction_evidence_json,
                    created_at,updated_at)
                   VALUES ('case:exp2','run:exp2',7,5,'repair schema','sig',
                           '[\"failure again\"]','completed','verified_success','pytest',
                           '{\"fixed\":true}',2,2)"""
            )
            await conn.commit()
        second = await CanonicalExperienceDistiller(self.repo._database).distill(7, "case:exp2")
        self.assertEqual(second["record_id"], result["record_id"])
        async with db.connect(self.path) as conn:
            async with conn.execute(
                "SELECT kind,algorithm_version,supporting_count,source_ids FROM memory_records WHERE record_id=?",
                (result["record_id"],),
            ) as cur:
                row = await cur.fetchone()
        self.assertEqual(row[:3], ("experience", "canonical-experience-v1", 2))
        self.assertEqual(json.loads(row[3]), ["case:exp", "case:exp2"])

    async def test_canonical_skill_compiler_requires_two_supporting_cases(self) -> None:
        from memory.application.skill_compilation import CanonicalSkillCompiler

        async with db.connect(self.path) as conn:
            await conn.execute(
                """INSERT INTO memory_records
                   (record_id,kind,group_id,bot_id,status,content,task_signature,
                    confidence,supporting_count,source_ids,created_at,updated_at)
                   VALUES ('exp:skill','experience',7,5,'active',?, 'repair',0.9,2,?,1,1)""",
                (json.dumps({"task_pattern": "repair SQLite", "verification": {"adapter": "pytest"}}),
                 json.dumps(["case:1", "case:2"])),
            )
            await conn.commit()
        from memory.application.skill_compilation import CanonicalSkillCompiler
        result = await CanonicalSkillCompiler(self.repo._database).compile(7, "exp:skill")
        self.assertTrue(result["compiled"])
        async with db.connect(self.path) as conn:
            async with conn.execute("SELECT maturity FROM skills WHERE skill_id=?", (result["skill_id"],)) as cur:
                self.assertEqual((await cur.fetchone())[0], "trial")

    async def test_compatibility_filter_does_not_claim_canonical_job_types(self) -> None:
        canonical_job = await self.repo.enqueue(self.scope, "observe_turn_fact", "input:3")
        deferred_job = await self.repo.enqueue(self.scope, "evaluate_case", "case:3")
        ready = await self.repo.list_ready(self.scope, limit=10)
        filtered = [
            row for row in ready if row["job_type"] in {"evaluate_case"}
        ]
        self.assertEqual([row["job_id"] for row in filtered], [deferred_job])
        self.assertNotEqual(canonical_job, deferred_job)


if __name__ == "__main__":
    unittest.main()
