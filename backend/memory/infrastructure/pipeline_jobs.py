"""SQLite persistence adapter for canonical Memory pipeline jobs."""
from __future__ import annotations

import json
import hashlib
import time
import uuid
from collections.abc import Mapping
from typing import Any

from memory.application.jobs import pipeline_job_identity
from memory.contracts import LostLeaseError, MemoryOperationError
from memory.domain import MemoryScope, ScopeKind
from memory.domain.safety import safe_memory_mapping, safe_memory_text
from memory.application.context import require_database
from memory.ports import MemoryDatabasePort, PipelineJobRepositoryPort


class CanonicalPipelineJobRepository:
    """Durable group-scoped repository for background Memory jobs."""

    def __init__(self, database: MemoryDatabasePort | None = None) -> None:
        self._database = database or require_database()

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
