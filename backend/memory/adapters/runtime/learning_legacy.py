"""Compatibility boundary for the existing durable learning pipeline."""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

import aiosqlite
from memory.contracts import (AssembleCase, CompleteExperienceUsage,
                              CompleteSkillUsage, MemoryOperationError,
                              MarkUsageAdopted, MarkUsageExecuted,
                              ProcessLearningCase, RecallExperiences,
                              RecallSkills, ResolveLearningRefs, VerifyUsage)
from memory.domain import MemoryScope, ScopeKind


class LegacyLearningAdapter:
    async def process_case(self, command: ProcessLearningCase) -> str:
        group_id = self._group_id(command.scope)
        from ai.pipeline import process_case
        return await process_case(
            command.case_id,
            group_id,
            input_version=command.input_version,
        )

    async def assemble_case(self, command: AssembleCase) -> str | None:
        group_id = self._group_id(command.scope)
        from ai.cases import assemble_case
        return await assemble_case(
            run_id=command.run_id,
            group_id=group_id,
            bot_id=command.scope.bot_id,
            task=command.task,
            outcome=command.outcome,
            tool_records=[dict(record) for record in command.tool_records],
        )

    async def recall_experiences(self, command: RecallExperiences) -> tuple[str, list[str]]:
        group_id = self._group_id(command.scope)
        from ai.experiences import recall_experiences
        try:
            return await recall_experiences(
                query=command.query,
                run_id=command.run_id,
                group_id=group_id,
                bot_id=command.scope.bot_id,
                limit=command.limit,
                char_budget=command.char_budget,
            )
        except aiosqlite.OperationalError:
            return "", []

    async def complete_experience_usage(self, command: CompleteExperienceUsage) -> None:
        group_id = self._group_id(command.scope)
        from ai.experiences import complete_usage
        await complete_usage(
            record_ids=list(command.record_ids),
            run_id=command.run_id,
            group_id=group_id,
            outcome=command.outcome,
            input_tokens=command.input_tokens,
            output_tokens=command.output_tokens,
            tool_attempts=command.tool_attempts,
        )

    async def recall_skills(self, command: RecallSkills) -> tuple[str, list[str]]:
        group_id = self._group_id(command.scope)
        from ai.skill_learning import recall_skills
        try:
            return await recall_skills(
                query=command.query,
                run_id=command.run_id,
                group_id=group_id,
                bot_id=command.scope.bot_id,
                limit=command.limit,
            )
        except aiosqlite.OperationalError:
            return "", []

    async def resolve_learning_refs(
        self, command: ResolveLearningRefs
    ) -> tuple[str, ...]:
        group_id = self._group_id(command.scope)
        if command.scope.bot_id is None:
            raise MemoryOperationError(
                "learning reference resolution requires bot scope"
            )
        from memory.application.references import experience_ref
        from ai.skill_learning import resolve_skill_refs

        experience_refs = tuple(
            experience_ref(record_id) for record_id in command.experience_ids
        )
        skill_refs = await resolve_skill_refs(
            skill_ids=list(command.skill_ids),
            group_id=group_id,
            bot_id=command.scope.bot_id,
        )
        return tuple(sorted((*experience_refs, *skill_refs)))

    async def complete_skill_usage(self, command: CompleteSkillUsage) -> None:
        group_id = self._group_id(command.scope)
        from ai.skill_learning import complete_skill_usage
        await complete_skill_usage(
            skill_ids=list(command.skill_ids),
            run_id=command.run_id,
            group_id=group_id,
            outcome=command.outcome,
        )

    async def mark_usage_adopted(self, command: MarkUsageAdopted) -> int:
        from ai.usage_tracking import mark_adopted
        return await mark_adopted(
            kind=command.kind,
            item_ids=command.item_ids,
            run_id=command.run_id,
            group_id=self._group_id(command.scope),
            adopted_via=command.adopted_via,
            evidence=command.evidence,
        )

    async def mark_usage_executed(self, command: MarkUsageExecuted) -> int:
        from ai.usage_tracking import mark_executed
        return await mark_executed(
            kind=command.kind,
            item_ids=command.item_ids,
            run_id=command.run_id,
            group_id=self._group_id(command.scope),
            evidence=command.evidence,
        )

    async def verify_usage(self, command: VerifyUsage) -> int:
        from ai.usage_tracking import mark_verified
        changed = await mark_verified(
            kind=command.kind,
            item_ids=command.item_ids,
            run_id=command.run_id,
            group_id=self._group_id(command.scope),
            status=command.status,
            evidence=command.evidence,
        )
        if changed and command.kind.value == "skill":
            from ai.skill_learning import project_skill
            group_id = self._group_id(command.scope)
            for skill_id in command.item_ids:
                await project_skill(skill_id, group_id)
        return changed

    @staticmethod
    def _group_id(scope: MemoryScope) -> int:
        if scope.kind not in (ScopeKind.GROUP, ScopeKind.BOT) or scope.group_id is None:
            raise MemoryOperationError("learning operation requires group scope")
        return scope.group_id


class LegacyPipelineJobAdapter:
    """Repository adapter for durable pipeline jobs stored in group SQLite."""

    async def enqueue(self, scope: MemoryScope, job_type: str, input_id: str, input_version: str = "1") -> str:
        group_id = self._group_id(scope)
        # This raw key format and the derived ID are persisted API: changing
        # either creates a second job for inputs already queued by older
        # releases. Keep the canonical representation stable across upgrades.
        key = f"{job_type}:{group_id}:{input_id}:{input_version}"
        job_id = "job:" + hashlib.sha256(key.encode()).hexdigest()[:24]
        now = int(time.time() * 1000)
        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            await db.execute("""INSERT INTO pipeline_jobs
              (job_id,job_type,group_id,input_id,input_version,idempotency_key,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING""",
              (job_id, job_type, group_id, input_id, input_version, key, now, now))
            await db.commit()
        return job_id

    async def list_ready(self, scope: MemoryScope, limit: int = 10) -> list[dict[str, Any]]:
        """Read claim candidates without taking an idle-group writer lock."""
        group_id = self._group_id(scope)
        now = int(time.time() * 1000)
        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=False) as db:
            async with db.execute(
                """SELECT job_id,job_type,input_id,input_version,status,attempt,max_attempts
                   FROM pipeline_jobs
                   WHERE group_id=? AND
                     ((attempt<max_attempts AND status IN ('pending','failed')) OR
                      (status='running' AND lease_until<?))
                   ORDER BY created_at,job_id LIMIT ?""",
                (group_id, now, max(1, limit)),
            ) as cur:
                rows = await cur.fetchall()
        columns = (
            "job_id", "job_type", "input_id", "input_version",
            "status", "attempt", "max_attempts",
        )
        return [dict(zip(columns, row)) for row in rows]

    async def claim(self, scope: MemoryScope, job_id: str, lease_seconds: int = 60) -> str | None:
        group_id = self._group_id(scope)
        now = int(time.time() * 1000)
        lease_until = now + lease_seconds * 1000
        lease_token = f"fence:{uuid.uuid4().hex[:12]}"
        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            await db.execute(
                """UPDATE pipeline_jobs
                   SET status='dead',lease_until=NULL,lease_token=NULL,
                       error=CASE WHEN error='' THEN 'lease expired after final attempt' ELSE error END,
                       updated_at=?
                   WHERE job_id=? AND group_id=? AND status='running'
                     AND lease_until<? AND attempt>=max_attempts""",
                (now, job_id, group_id, now),
            )
            cur = await db.execute("""UPDATE pipeline_jobs SET status='running',attempt=attempt+1,
              lease_until=?,lease_token=?,updated_at=? WHERE job_id=? AND group_id=? AND
              (status='pending' OR (status='running' AND lease_until<?) OR status='failed') AND attempt<max_attempts""",
              (lease_until, lease_token, now, job_id, group_id, now))
            await db.commit()
            return lease_token if cur.rowcount == 1 else None

    async def complete(self, scope: MemoryScope, job_id: str, lease_token: str, output_json: str = "{}") -> bool:
        if not lease_token:
            return False
        group_id = self._group_id(scope)
        now = int(time.time() * 1000)
        query = ("UPDATE pipeline_jobs SET status='completed',lease_until=NULL,lease_token=NULL,error='',output_json=?,"
                 "completed_at=?,updated_at=? WHERE job_id=? AND group_id=? AND status='running' AND lease_token=?")
        params: list[Any] = [output_json, now, now, job_id, group_id, lease_token]

        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            cur = await db.execute(query, params)
            await db.commit()
            return cur.rowcount == 1

    async def fail(self, scope: MemoryScope, job_id: str, lease_token: str, error_message: str) -> bool:
        if not lease_token:
            return False
        group_id = self._group_id(scope)
        now = int(time.time() * 1000)
        query = ("UPDATE pipeline_jobs SET status=CASE WHEN attempt>=max_attempts THEN 'dead' "
                 "ELSE 'failed' END,lease_until=NULL,lease_token=NULL,error=?,updated_at=? WHERE job_id=? AND group_id=? AND status='running' AND lease_token=?")
        params: list[Any] = [error_message[:2000], now, job_id, group_id, lease_token]

        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            cur = await db.execute(query, params)
            await db.commit()
            return cur.rowcount == 1

    async def stats(self, scope: MemoryScope) -> dict[str, int]:
        group_id = self._group_id(scope)
        now = int(time.time() * 1000)
        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=False) as db:
            async with db.execute(
                """SELECT
                     SUM(CASE WHEN status IN ('pending','failed') OR
                                      (status='running' AND lease_until<?)
                              THEN 1 ELSE 0 END),
                     SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN status='dead' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN status='running' AND lease_until<?
                              THEN 1 ELSE 0 END)
                   FROM pipeline_jobs WHERE group_id=?""",
                (now, now, group_id),
            ) as cur:
                row = await cur.fetchone()
        values = row or (0, 0, 0, 0)
        return {
            "backlog": int(values[0] or 0),
            "retry": int(values[1] or 0),
            "dead": int(values[2] or 0),
            "expired_lease": int(values[3] or 0),
        }

    @staticmethod
    def _group_id(scope: MemoryScope) -> int:
        if scope.kind not in (ScopeKind.GROUP, ScopeKind.BOT) or scope.group_id is None:
            raise MemoryOperationError("pipeline job operation requires group scope")
        return scope.group_id
