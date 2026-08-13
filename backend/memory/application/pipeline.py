"""Canonical durable pipeline storage.

This module owns the persistence mechanics of Memory learning jobs.  It is
deliberately independent from ``backend.ai`` so the dispatcher can be moved
without changing idempotency, lease fencing, or retry behavior.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from memory.application.jobs import pipeline_job_identity
from memory.contracts import LostLeaseError, MemoryOperationError
from memory.domain import MemoryScope, ScopeKind
from memory.infrastructure import (
    SQLiteMemoryDatabase, safe_memory_mapping, safe_memory_text,
)
from memory.ports import PipelineJobRepositoryPort

log = logging.getLogger(__name__)


class CanonicalPipelineJobRepository:
    """Durable group-scoped repository for background Memory jobs."""

    def __init__(self, database: SQLiteMemoryDatabase | None = None) -> None:
        self._database = database or SQLiteMemoryDatabase()

    async def enqueue(
        self, scope: MemoryScope, job_type: str, input_id: str,
        input_version: str = "1",
    ) -> str:
        group_id = _group_id(scope)
        job_id, key = pipeline_job_identity(job_type, group_id, input_id, input_version)
        now = _now_ms()
        async with await self._database.connect("pipeline_jobs", group_id, write=True) as db:
            await db.execute(
                """INSERT INTO pipeline_jobs
                   (job_id,job_type,group_id,input_id,input_version,idempotency_key,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(idempotency_key) DO NOTHING""",
                (job_id, job_type, group_id, input_id, input_version, key, now, now),
            )
            await db.commit()
        return job_id

    async def list_ready(
        self, scope: MemoryScope, limit: int = 10
    ) -> list[dict[str, Any]]:
        group_id = _group_id(scope)
        now = _now_ms()
        async with await self._database.connect("pipeline_jobs", group_id, write=False) as db:
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
        columns = ("job_id", "job_type", "input_id", "input_version",
                   "status", "attempt", "max_attempts")
        return [dict(zip(columns, row)) for row in rows]

    async def claim(
        self, scope: MemoryScope, job_id: str, lease_seconds: int = 60
    ) -> str | None:
        group_id = _group_id(scope)
        now = _now_ms()
        lease_until = now + max(1, lease_seconds) * 1000
        lease_token = f"fence:{uuid.uuid4().hex[:12]}"
        async with await self._database.connect("pipeline_jobs", group_id, write=True) as db:
            await db.execute(
                """UPDATE pipeline_jobs
                   SET status='dead',lease_until=NULL,lease_token=NULL,
                       error=CASE WHEN error='' THEN 'lease expired after final attempt' ELSE error END,
                       updated_at=?
                   WHERE job_id=? AND group_id=? AND status='running'
                     AND lease_until<? AND attempt>=max_attempts""",
                (now, job_id, group_id, now),
            )
            cur = await db.execute(
                """UPDATE pipeline_jobs SET status='running',attempt=attempt+1,
                   lease_until=?,lease_token=?,updated_at=?
                   WHERE job_id=? AND group_id=?
                     AND (status='pending' OR (status='running' AND lease_until<?) OR status='failed')
                     AND attempt<max_attempts""",
                (lease_until, lease_token, now, job_id, group_id, now),
            )
            await db.commit()
        return lease_token if cur.rowcount == 1 else None

    async def complete_with_checkpoint(
        self, scope: MemoryScope, job_id: str, lease_token: str,
        output_json: str, *, thread_id: str, state: Mapping[str, Any],
        parent_checkpoint_id: str | None = None,
    ) -> bool:
        """Commit job completion and its terminal checkpoint atomically."""
        if not lease_token:
            return False
        group_id = _group_id(scope)
        now = _now_ms()
        safe_state = json.loads(safe_memory_mapping(state))
        raw_json = json.dumps(safe_state, sort_keys=True, default=str)
        state_hash = hashlib.sha256(raw_json.encode()).hexdigest()[:16]
        checkpoint_id = f"chk:{thread_id}:completed:{state_hash[:8]}"
        async with await self._database.connect("pipeline_jobs", group_id, write=True) as db:
            await _ensure_checkpoint_tables(db)
            cur = await db.execute(
                """UPDATE pipeline_jobs SET status='completed',lease_until=NULL,
                   lease_token=NULL,error='',output_json=?,completed_at=?,updated_at=?
                   WHERE job_id=? AND group_id=? AND status='running' AND lease_token=?""",
                (safe_memory_text(output_json, limit=100_000), now, now,
                 job_id, group_id, lease_token),
            )
            if cur.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                """INSERT OR IGNORE INTO memory_checkpoints
                   (checkpoint_id,group_id,thread_id,parent_checkpoint_id,step_name,
                    state_hash,state_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (checkpoint_id, group_id, thread_id, parent_checkpoint_id, "completed",
                 state_hash, json.dumps(safe_state, ensure_ascii=False, sort_keys=True), now),
            )
            await db.commit()
        return True

    async def renew_lease(
        self, scope: MemoryScope, job_id: str, lease_token: str,
        lease_seconds: int = 60,
    ) -> bool:
        if not lease_token:
            return False
        group_id = _group_id(scope)
        now = _now_ms()
        async with await self._database.connect("pipeline_jobs", group_id, write=True) as db:
            cur = await db.execute(
                """UPDATE pipeline_jobs SET lease_until=?,updated_at=?
                   WHERE job_id=? AND group_id=? AND status='running' AND lease_token=?""",
                (now + max(1, lease_seconds) * 1000, now, job_id, group_id, lease_token),
            )
            await db.commit()
        return cur.rowcount == 1

    async def fail(
        self, scope: MemoryScope, job_id: str, lease_token: str,
        error_message: str,
    ) -> bool:
        if not lease_token:
            return False
        group_id = _group_id(scope)
        now = _now_ms()
        async with await self._database.connect("pipeline_jobs", group_id, write=True) as db:
            cur = await db.execute(
                """UPDATE pipeline_jobs SET status=CASE WHEN attempt>=max_attempts
                   THEN 'dead' ELSE 'failed' END,lease_until=NULL,lease_token=NULL,
                   error=?,updated_at=?
                   WHERE job_id=? AND group_id=? AND status='running' AND lease_token=?""",
                (safe_memory_text(error_message, limit=2000), now,
                 job_id, group_id, lease_token),
            )
            await db.commit()
        return cur.rowcount == 1

    async def defer(self, scope: MemoryScope, job_id: str, lease_token: str) -> bool:
        """Return a claimed job to pending without consuming a retry attempt."""
        if not lease_token:
            return False
        group_id = _group_id(scope)
        now = _now_ms()
        async with await self._database.connect("pipeline_jobs", group_id, write=True) as db:
            cur = await db.execute(
                """UPDATE pipeline_jobs SET status='pending',attempt=MAX(attempt-1,0),
                   lease_until=NULL,lease_token=NULL,updated_at=?
                   WHERE job_id=? AND group_id=? AND status='running' AND lease_token=?""",
                (now, job_id, group_id, lease_token),
            )
            await db.commit()
        return cur.rowcount == 1

    async def stats(self, scope: MemoryScope) -> dict[str, int]:
        group_id = _group_id(scope)
        now = _now_ms()
        async with await self._database.connect("pipeline_jobs", group_id, write=False) as db:
            async with db.execute(
                """SELECT
                     SUM(CASE WHEN status IN ('pending','failed') OR
                                      (status='running' AND lease_until<?) THEN 1 ELSE 0 END),
                     SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN status='dead' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN status='running' AND lease_until<? THEN 1 ELSE 0 END)
                   FROM pipeline_jobs WHERE group_id=?""",
                (now, now, group_id),
            ) as cur:
                row = await cur.fetchone()
        values = row or (0, 0, 0, 0)
        return {"backlog": int(values[0] or 0), "retry": int(values[1] or 0),
                "dead": int(values[2] or 0), "expired_lease": int(values[3] or 0)}

    async def checkpoint(
        self, scope: MemoryScope, thread_id: str, step_name: str,
        state: Mapping[str, Any], parent_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a bounded DAG checkpoint in the canonical group database."""
        group_id = _group_id(scope)
        safe_state = json.loads(safe_memory_mapping(state))
        raw_json = json.dumps(safe_state, sort_keys=True, default=str)
        state_hash = hashlib.sha256(raw_json.encode()).hexdigest()[:16]
        checkpoint_id = f"chk:{thread_id}:{step_name}:{state_hash[:8]}"
        created_at = time.time()
        async with await self._database.connect("memory_checkpoints", group_id, write=True) as db:
            await _ensure_checkpoint_tables(db)
            await db.execute(
                """INSERT OR IGNORE INTO memory_checkpoints
                   (checkpoint_id,group_id,thread_id,parent_checkpoint_id,step_name,
                    state_hash,state_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (checkpoint_id, group_id, thread_id, parent_checkpoint_id, step_name,
                 state_hash, json.dumps(safe_state, ensure_ascii=False, sort_keys=True),
                 int(created_at * 1000)),
            )
            await db.commit()
        return {
            "checkpoint_id": checkpoint_id,
            "thread_id": thread_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "step_name": step_name,
            "state_hash": state_hash,
            "state": safe_state,
        }

    async def latest_checkpoint(
        self, scope: MemoryScope, thread_id: str
    ) -> dict[str, Any] | None:
        group_id = _group_id(scope)
        async with await self._database.connect("memory_checkpoints", group_id, write=False) as db:
            await _ensure_checkpoint_tables(db)
            async with db.execute(
                """SELECT checkpoint_id,parent_checkpoint_id,step_name,state_hash,
                          state_json,created_at FROM memory_checkpoints
                   WHERE group_id=? AND thread_id=?
                   ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1""",
                (group_id, thread_id),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return {
            "checkpoint_id": str(row[0]), "thread_id": thread_id,
            "parent_checkpoint_id": row[1], "step_name": str(row[2]),
            "state_hash": str(row[3]), "state": json.loads(row[4] or "{}"),
            "created_at": int(row[5]),
        }


PipelineHandler = Callable[[int, str, str], Awaitable[Mapping[str, Any]]]


class RetryablePipelineJob(Exception):
    """Signal that a transient condition should return the job to the queue."""


class CanonicalPipelineDispatcher:
    """Execute canonical jobs with injected, already-migrated handlers."""

    def __init__(
        self, repository: PipelineJobRepositoryPort | None = None,
        handlers: Mapping[str, PipelineHandler] = (),
    ) -> None:
        self.repository = repository or CanonicalPipelineJobRepository()
        self.handlers = dict(handlers)

    async def dispatch_group(
        self, group_id: int, *, limit: int = 10, lease_seconds: int = 60,
    ) -> dict[str, int]:
        scope = MemoryScope.group(group_id=group_id, actor_id="service:canonical_pipeline")
        jobs = await self.repository.list_ready(scope, limit=limit)
        processed = failed = 0
        for job in jobs:
            # The composition root supplies the complete canonical handler map.
            if str(job["job_type"]) not in self.handlers:
                continue
            job_id = str(job["job_id"])
            token = await self.repository.claim(scope, job_id, lease_seconds)
            if not token:
                continue
            try:
                checkpoint_scope = MemoryScope.group(
                    group_id=group_id, actor_id="service:canonical_pipeline_checkpoint"
                )
                previous = await self.repository.latest_checkpoint(checkpoint_scope, job_id)
                claimed = await self.repository.checkpoint(
                    checkpoint_scope, job_id, "claimed", {
                        "job_id": job_id, "job_type": str(job["job_type"]),
                        "input_id": str(job["input_id"]),
                        "input_version": str(job["input_version"]),
                        "attempt": int(job["attempt"]) + 1, "status": "running",
                    }, previous.get("checkpoint_id") if previous else None,
                )
                handler = self.handlers.get(str(job["job_type"]))
                if handler is None:
                    raise ValueError(f"unsupported pipeline job type: {job['job_type']}")
                handler_task = asyncio.create_task(
                    handler(group_id, str(job["input_id"]), str(job["input_version"]))
                )
                heartbeat_lost = asyncio.Event()

                async def _heartbeat() -> None:
                    while True:
                        await asyncio.sleep(max(0.1, lease_seconds / 3))
                        try:
                            renewed = await self.repository.renew_lease(scope, job_id, token, lease_seconds)
                        except Exception:
                            renewed = False
                        if not renewed:
                            heartbeat_lost.set()
                            handler_task.cancel()
                            return

                heartbeat_task = asyncio.create_task(_heartbeat())
                try:
                    output = dict(await handler_task)
                except asyncio.CancelledError:
                    if heartbeat_lost.is_set():
                        raise LostLeaseError(f"Worker lost lease for job {job_id}")
                    raise
                finally:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
                if heartbeat_lost.is_set():
                    raise LostLeaseError(f"Worker lost lease for job {job_id}")
                if not await self.repository.complete_with_checkpoint(
                    scope, job_id, token, json.dumps(output, ensure_ascii=False),
                    thread_id=job_id,
                    state={"job_id": job_id, "job_type": str(job["job_type"]),
                           "input_id": str(job["input_id"]), "status": "completed",
                           "output": output},
                    parent_checkpoint_id=claimed["checkpoint_id"],
                ):
                    raise LostLeaseError(f"Worker lost lease for job {job_id}")
                processed += 1
            except Exception as exc:
                if isinstance(exc, RetryablePipelineJob):
                    await self.repository.defer(scope, job_id, token)
                    continue
                failed += 1
                log.exception("canonical memory pipeline job failed: %s", job_id)
                await self.repository.fail(scope, job_id, token, str(exc))
        return {"claimed": processed + failed, "completed": processed, "failed": failed}


async def _ensure_checkpoint_tables(db: Any) -> None:
    await db.execute(
        """CREATE TABLE IF NOT EXISTS memory_checkpoints (
           checkpoint_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
           thread_id TEXT NOT NULL, parent_checkpoint_id TEXT,
           step_name TEXT NOT NULL, state_hash TEXT NOT NULL,
           state_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL)"""
    )
    await db.execute(
        """CREATE INDEX IF NOT EXISTS idx_memory_checkpoints_thread
           ON memory_checkpoints(group_id,thread_id,created_at)"""
    )


def _group_id(scope: MemoryScope) -> int:
    if scope.kind not in (ScopeKind.GROUP, ScopeKind.BOT) or scope.group_id is None:
        raise MemoryOperationError("pipeline job operation requires group scope")
    return scope.group_id


def _now_ms() -> int:
    return int(time.time() * 1000)
