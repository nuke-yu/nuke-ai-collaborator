"""Compatibility boundary for the existing durable learning pipeline."""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

import aiosqlite
from memory.contracts import (AssembleCase, CompleteExperienceUsage,
                              CompleteSkillUsage, MemoryOperationError,
                              ProcessLearningCase, RecallExperiences,
                              RecallSkills)
from memory.domain import MemoryScope, ScopeKind


class LegacyLearningAdapter:
    async def process_case(self, command: ProcessLearningCase) -> str:
        group_id = self._group_id(command.scope)
        from ai.pipeline import process_case
        return await process_case(command.case_id, group_id)

    async def assemble_case(self, command: AssembleCase) -> str | None:
        group_id = self._group_id(command.scope)
        from ai.cases import assemble_case
        return await assemble_case(
            run_id=command.run_id,
            group_id=group_id,
            bot_id=command.scope.bot_id,
            task=command.task,
            outcome=command.outcome,
            tool_records=list(command.tool_records),
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

    async def complete_skill_usage(self, command: CompleteSkillUsage) -> None:
        group_id = self._group_id(command.scope)
        from ai.skill_learning import complete_skill_usage
        await complete_skill_usage(
            skill_ids=list(command.skill_ids),
            run_id=command.run_id,
            group_id=group_id,
            outcome=command.outcome,
        )

    @staticmethod
    def _group_id(scope: MemoryScope) -> int:
        if scope.kind not in (ScopeKind.GROUP, ScopeKind.BOT) or scope.group_id is None:
            raise MemoryOperationError("learning operation requires group scope")
        return scope.group_id


class LegacyPipelineJobAdapter:
    """Repository adapter for durable pipeline jobs stored in group SQLite."""

    async def enqueue(self, scope: MemoryScope, job_type: str, input_id: str, input_version: str = "1") -> str:
        group_id = self._group_id(scope)
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

    async def claim(self, scope: MemoryScope, job_id: str, lease_seconds: int = 60) -> str | None:
        group_id = self._group_id(scope)
        now = int(time.time() * 1000)
        lease_until = now + lease_seconds * 1000
        lease_token = f"fence:{uuid.uuid4().hex[:12]}"
        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            cur = await db.execute("""UPDATE pipeline_jobs SET status='running',attempt=attempt+1,
              lease_until=?,lease_token=?,updated_at=? WHERE job_id=? AND group_id=? AND
              (status='pending' OR (status='running' AND lease_until<?) OR status='failed') AND attempt<max_attempts""",
              (lease_until, lease_token, now, job_id, group_id, now))
            await db.commit()
            return lease_token if cur.rowcount == 1 else None

    async def complete(self, scope: MemoryScope, job_id: str, output_json: str = "{}", lease_token: str | None = None) -> bool:
        group_id = self._group_id(scope)
        now = int(time.time() * 1000)
        query = ("UPDATE pipeline_jobs SET status='completed',lease_until=NULL,lease_token=NULL,error='',output_json=?,"
                 "completed_at=?,updated_at=? WHERE job_id=? AND group_id=? AND status='running'")
        params: list[Any] = [output_json, now, now, job_id, group_id]
        if lease_token is not None:
            query += " AND lease_token=?"
            params.append(lease_token)

        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            cur = await db.execute(query, params)
            await db.commit()
            return cur.rowcount == 1

    async def fail(self, scope: MemoryScope, job_id: str, error_message: str, max_attempts: int = 3, lease_token: str | None = None) -> bool:
        group_id = self._group_id(scope)
        now = int(time.time() * 1000)
        query = ("UPDATE pipeline_jobs SET status=CASE WHEN attempt>=max_attempts THEN 'dead' "
                 "ELSE 'failed' END,lease_until=NULL,lease_token=NULL,error=?,updated_at=? WHERE job_id=? AND group_id=? AND status='running'")
        params: list[Any] = [error_message[:2000], now, job_id, group_id]
        if lease_token is not None:
            query += " AND lease_token=?"
            params.append(lease_token)

        from ai.memory import _memory_db
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            cur = await db.execute(query, params)
            await db.commit()
            return cur.rowcount == 1

    @staticmethod
    def _group_id(scope: MemoryScope) -> int:
        if scope.kind not in (ScopeKind.GROUP, ScopeKind.BOT) or scope.group_id is None:
            raise MemoryOperationError("pipeline job operation requires group scope")
        return scope.group_id


