"""Compatibility boundary for the existing durable learning pipeline."""
from __future__ import annotations

import time
import uuid
from typing import Any

import aiosqlite
from memory.contracts import (ApproveSkillCandidate, AssembleCase,
                              CompleteExperienceUsage,
                              CompleteSkillUsage, MemoryOperationError,
                              MarkUsageAdopted, MarkUsageExecuted,
                              ListSkillCandidates,
                              ProcessLearningCase, RecallExperiences,
                              RecallSkills, ResolveLearningRefs,
                              SkillCandidate, VerifyUsage)
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

    async def list_skill_candidates(
        self, command: ListSkillCandidates
    ) -> tuple[SkillCandidate, ...]:
        group_id = self._group_id(command.scope)
        if command.scope.bot_id is None:
            raise MemoryOperationError("Skill candidates require bot scope")
        from ai.skill_learning import list_skill_candidates

        rows = await list_skill_candidates(
            group_id=group_id,
            bot_id=command.scope.bot_id,
        )
        return tuple(SkillCandidate(**row) for row in rows)

    async def approve_skill_candidate(
        self, command: ApproveSkillCandidate
    ) -> bool:
        group_id = self._group_id(command.scope)
        if command.scope.bot_id is None or command.scope.user_id is None:
            raise MemoryOperationError("Skill approval requires user bot scope")
        from ai.skill_learning import promote_skill

        return await promote_skill(
            command.skill_id,
            group_id,
            "active",
            bot_id=command.scope.bot_id,
            actor_id=command.scope.actor_id,
            reason=command.reason,
        )

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
        from memory.application.jobs import pipeline_job_identity

        job_id, key = pipeline_job_identity(
            job_type, group_id, input_id, input_version
        )
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

    async def checkpoint(
        self,
        scope: MemoryScope,
        thread_id: str,
        step_name: str,
        state: dict[str, Any],
        parent_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a LangGraph-compatible checkpoint for a durable job."""
        group_id = self._group_id(scope)
        from memory.adapters.algorithms import LangGraphDAGEngine

        checkpoint = LangGraphDAGEngine().create_checkpoint(
            thread_id=thread_id,
            step_name=step_name,
            state=state,
            parent_id=parent_checkpoint_id,
        )
        import json

        async with await self._db(group_id, write=True) as db:
            await self._ensure_checkpoint_table(db)
            await db.execute(
                """INSERT OR IGNORE INTO memory_checkpoints
                   (checkpoint_id,group_id,thread_id,parent_checkpoint_id,
                    step_name,state_hash,state_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    checkpoint.checkpoint_id,
                    group_id,
                    checkpoint.thread_id,
                    checkpoint.parent_checkpoint_id,
                    checkpoint.step_name,
                    checkpoint.state_hash,
                    json.dumps(checkpoint.state_payload, ensure_ascii=False, sort_keys=True),
                    int(checkpoint.created_at * 1000),
                ),
            )
            await db.commit()
        return {
            "checkpoint_id": checkpoint.checkpoint_id,
            "thread_id": checkpoint.thread_id,
            "parent_checkpoint_id": checkpoint.parent_checkpoint_id,
            "step_name": checkpoint.step_name,
            "state_hash": checkpoint.state_hash,
            "state": checkpoint.state_payload,
        }

    async def latest_checkpoint(
        self, scope: MemoryScope, thread_id: str
    ) -> dict[str, Any] | None:
        """Load the newest persisted checkpoint for a group/thread."""
        group_id = self._group_id(scope)
        import json

        async with await self._db(group_id, write=False) as db:
            await self._ensure_checkpoint_table(db)
            async with db.execute(
                """SELECT checkpoint_id,parent_checkpoint_id,step_name,
                          state_hash,state_json,created_at
                   FROM memory_checkpoints
                   WHERE group_id=? AND thread_id=?
                   ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1""",
                (group_id, thread_id),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return {
            "checkpoint_id": str(row[0]),
            "thread_id": thread_id,
            "parent_checkpoint_id": row[1],
            "step_name": str(row[2]),
            "state_hash": str(row[3]),
            "state": json.loads(row[4] or "{}"),
            "created_at": int(row[5]),
        }

    async def prune_checkpoints(
        self, scope: MemoryScope, thread_id: str, *, keep: int = 20
    ) -> int:
        """Delete old checkpoints while retaining the newest lineage tail."""
        if keep < 1:
            raise ValueError("keep must be positive")
        group_id = self._group_id(scope)
        async with await self._db(group_id, write=True) as db:
            await self._ensure_checkpoint_table(db)
            async with db.execute(
                """SELECT checkpoint_id FROM memory_checkpoints
                   WHERE group_id=? AND thread_id=?
                   ORDER BY created_at DESC,checkpoint_id DESC""",
                (group_id, thread_id),
            ) as cur:
                checkpoint_ids = [str(row[0]) for row in await cur.fetchall()]
            stale_ids = checkpoint_ids[keep:]
            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                await db.execute(
                    f"DELETE FROM memory_checkpoints WHERE group_id=? "
                    f"AND checkpoint_id IN ({placeholders})",
                    (group_id, *stale_ids),
                )
            await db.commit()
        return len(stale_ids)

    async def delete_checkpoint_thread(
        self, scope: MemoryScope, thread_id: str
    ) -> int:
        """Delete all checkpoints belonging to one durable execution thread."""
        group_id = self._group_id(scope)
        async with await self._db(group_id, write=True) as db:
            await self._ensure_checkpoint_table(db)
            cur = await db.execute(
                "DELETE FROM memory_checkpoints WHERE group_id=? AND thread_id=?",
                (group_id, thread_id),
            )
            await db.commit()
        return int(cur.rowcount)

    async def put_pending_write(
        self,
        scope: MemoryScope,
        checkpoint_id: str,
        task_id: str,
        channel: str,
        value: Any,
    ) -> str:
        """Persist a channel write that happened before a checkpoint commit.

        LangGraph uses pending writes to make tool/task side effects recoverable
        when a worker dies between the task and the next checkpoint.
        """
        if not checkpoint_id or not task_id or not channel:
            raise ValueError("checkpoint_id, task_id and channel are required")
        group_id = self._group_id(scope)
        import hashlib
        import json

        write_id = "pwrite:" + hashlib.sha256(
            f"{group_id}:{checkpoint_id}:{task_id}:{channel}".encode()
        ).hexdigest()[:24]
        now = int(time.time() * 1000)
        async with await self._db(group_id, write=True) as db:
            await self._ensure_checkpoint_table(db)
            await db.execute(
                """INSERT INTO memory_checkpoint_pending_writes
                   (write_id,group_id,checkpoint_id,task_id,channel,value_json,created_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(group_id,checkpoint_id,task_id,channel)
                   DO UPDATE SET value_json=excluded.value_json,created_at=excluded.created_at""",
                (write_id, group_id, checkpoint_id, task_id, channel,
                 json.dumps(value, ensure_ascii=False, sort_keys=True, default=str), now),
            )
            await db.commit()
        return write_id

    async def list_pending_writes(
        self, scope: MemoryScope, checkpoint_id: str
    ) -> list[dict[str, Any]]:
        """Return pending writes in deterministic creation order for recovery."""
        group_id = self._group_id(scope)
        import json

        async with await self._db(group_id, write=False) as db:
            await self._ensure_checkpoint_table(db)
            async with db.execute(
                """SELECT write_id,task_id,channel,value_json,created_at
                   FROM memory_checkpoint_pending_writes
                   WHERE group_id=? AND checkpoint_id=? ORDER BY created_at,write_id""",
                (group_id, checkpoint_id),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {"write_id": str(row[0]), "task_id": str(row[1]), "channel": str(row[2]),
             "value": json.loads(row[3] or "null"), "created_at": int(row[4])}
            for row in rows
        ]

    async def acknowledge_pending_writes(
        self, scope: MemoryScope, checkpoint_id: str, task_id: str | None = None
    ) -> int:
        """Remove recovered writes after they have been incorporated in a checkpoint."""
        group_id = self._group_id(scope)
        async with await self._db(group_id, write=True) as db:
            await self._ensure_checkpoint_table(db)
            if task_id:
                cur = await db.execute(
                    "DELETE FROM memory_checkpoint_pending_writes WHERE group_id=? AND checkpoint_id=? AND task_id=?",
                    (group_id, checkpoint_id, task_id),
                )
            else:
                cur = await db.execute(
                    "DELETE FROM memory_checkpoint_pending_writes WHERE group_id=? AND checkpoint_id=?",
                    (group_id, checkpoint_id),
                )
            await db.commit()
        return int(cur.rowcount)

    async def fork_checkpoint(
        self,
        scope: MemoryScope,
        checkpoint_id: str,
        new_thread_id: str,
        step_name: str = "fork",
    ) -> dict[str, Any] | None:
        """Create a new branch whose parent is an existing checkpoint."""
        group_id = self._group_id(scope)
        import json
        async with await self._db(group_id, write=True) as db:
            await self._ensure_checkpoint_table(db)
            async with db.execute(
                "SELECT state_json FROM memory_checkpoints WHERE group_id=? AND checkpoint_id=?",
                (group_id, checkpoint_id),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            from memory.adapters.algorithms import LangGraphDAGEngine
            checkpoint = LangGraphDAGEngine().create_checkpoint(
                thread_id=new_thread_id, step_name=step_name,
                state=json.loads(row[0] or "{}"), parent_id=checkpoint_id,
            )
            await db.execute(
                """INSERT OR IGNORE INTO memory_checkpoints
                   (checkpoint_id,group_id,thread_id,parent_checkpoint_id,step_name,state_hash,state_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (checkpoint.checkpoint_id, group_id, checkpoint.thread_id,
                 checkpoint.parent_checkpoint_id, checkpoint.step_name,
                 checkpoint.state_hash, json.dumps(checkpoint.state_payload, ensure_ascii=False, sort_keys=True),
                 int(checkpoint.created_at * 1000)),
            )
            await db.commit()
        return {"checkpoint_id": checkpoint.checkpoint_id, "thread_id": new_thread_id,
                "parent_checkpoint_id": checkpoint_id, "step_name": step_name,
                "state_hash": checkpoint.state_hash, "state": checkpoint.state_payload}

    async def _db(self, group_id: int, *, write: bool):
        from ai.memory import _memory_db

        return await _memory_db("memory_checkpoints", group_id, write=write)

    @staticmethod
    async def _ensure_checkpoint_table(db) -> None:
        """Lazy-create the table for legacy group DBs during rollout."""
        await db.execute(
            """CREATE TABLE IF NOT EXISTS memory_checkpoints (
                checkpoint_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
                thread_id TEXT NOT NULL, parent_checkpoint_id TEXT,
                step_name TEXT NOT NULL, state_hash TEXT NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_memory_checkpoints_thread
               ON memory_checkpoints(group_id,thread_id,created_at)"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS memory_checkpoint_pending_writes (
                write_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
                checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL,
                channel TEXT NOT NULL, value_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                UNIQUE(group_id,checkpoint_id,task_id,channel)
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_memory_checkpoint_pending_writes
               ON memory_checkpoint_pending_writes(group_id,checkpoint_id,created_at)"""
        )

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
