"""
plugins/agent_dashboard/task_store.py — Persistent storage for agent tasks.

P1-1: Replaces in-memory _tasks dict with database-backed storage.
All task state is persisted to the agent_tasks table and survives
process restarts.

Usage:
    from plugins.agent_dashboard.task_store import TaskStore

    store = TaskStore()
    await store.create_task(task_id, group_id, bot_id, ...)
    task = await store.get_task(task_id)
    await store.update_status(task_id, "running")
"""
import asyncio
import logging
import random
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


_ALLOWED_TRANSITIONS = {
    "created": {
        "dispatched", "running", "paused", "stuck", "retrying",
        "completed", "failed", "aborted", "stuck_permanently",
    },
    "dispatched": {
        "running", "paused", "stuck", "retrying", "completed", "failed", "aborted",
    },
    "running": {"paused", "stuck", "retrying", "completed", "failed", "aborted"},
    "paused": {"running", "stuck", "retrying", "completed", "failed", "aborted"},
    "stuck": {"retrying", "failed", "aborted", "stuck_permanently"},
    "retrying": {"running", "restarted", "failed", "aborted", "stuck_permanently"},
    "restarted": {
        "running", "paused", "stuck", "retrying", "completed", "failed", "aborted",
    },
    "failed": {"retrying", "aborted"},
    "aborted": {"retrying"},
    "stuck_permanently": {"retrying", "aborted"},
    "completed": set(),
}
_PERSISTED_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "aborted", "stuck_permanently"}
)
_RETRY_CLAIM_TTL_SECONDS = 120


@dataclass(frozen=True)
class RetryClaim:
    token: str
    previous_status: str


class IdempotencyConflict(RuntimeError):
    pass


class TaskCreationInProgress(RuntimeError):
    pass


class PreviousTaskCreationFailed(RuntimeError):
    pass


class TaskRetryConflict(RuntimeError):
    """The task is not in a state that the requested retry mode can claim."""

    def __init__(self, task_id: str, status: str):
        self.task_id = task_id
        self.status = status
        super().__init__(f"Task {task_id} cannot be retried from status {status}")


def _utc_iso(value) -> str:
    """Normalize SQLite timestamps to an explicit UTC API contract."""
    if isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace(" ", "T")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_to_task(row) -> dict:
    return {
        "task_id": row[0],
        "group_id": row[1],
        "bot_id": row[2],
        "repo_url": row[3],
        "requirements": row[4],
        "base_branch": row[5],
        "test_command": row[6],
        "model": row[7],
        "max_iterations": row[8],
        "status": row[9],
        "created_at": _utc_iso(row[10]),
        "updated_at": _utc_iso(row[11]),
        "pr_url": row[12],
        "error_message": row[13],
    }


class TaskStore:
    """Database-backed storage for agent tasks."""

    async def create_task(
        self,
        task_id: str,
        group_id: int,
        bot_id: int,
        repo_url: str,
        requirements: str,
        base_branch: str = "main",
        test_command: str = "",
        model: str = "deepseek-chat",
        max_iterations: int = 100,
    ) -> dict:
        """Create a new task record.

        Returns:
            dict with task_id, group_id, bot_id, status, created_at
        """
        from db import write_connect

        async with write_connect() as db:
            await db.execute(
                """
                INSERT INTO agent_tasks (
                    task_id, group_id, bot_id, repo_url, requirements,
                    base_branch, test_command, model, max_iterations, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'created')
                """,
                (
                    task_id, group_id, bot_id, repo_url, requirements,
                    base_branch, test_command, model, max_iterations,
                ),
            )
            await db.commit()

            # Fetch the created record to return with timestamps
            return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> Optional[dict]:
        """Get a task by ID.

        Returns:
            dict with all task fields, or None if not found
        """
        from db import connect

        async with connect() as db:
            cur = await db.execute(
                """
                SELECT task_id, group_id, bot_id, repo_url, requirements,
                       base_branch, test_command, model, max_iterations,
                       status, created_at, updated_at, pr_url, error_message
                FROM agent_tasks
                WHERE task_id = ?
                """,
                (task_id,),
            )
            row = await cur.fetchone()

        if not row:
            return None

        return _row_to_task(row)

    async def list_tasks(
        self,
        group_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """List tasks with optional filters.

        Args:
            group_id: Filter by group (optional)
            status: Filter by status (optional)
            limit: Max number of tasks to return

        Returns:
            List of task dicts
        """
        from db import connect

        query = """
            SELECT task_id, group_id, bot_id, repo_url, requirements,
                   base_branch, test_command, model, max_iterations,
                   status, created_at, updated_at, pr_url, error_message
            FROM agent_tasks
        """
        params = []
        conditions = []

        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)

        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with connect() as db:
            cur = await db.execute(query, params)
            rows = await cur.fetchall()

        return [_row_to_task(row) for row in rows]

    async def update_status(
        self,
        task_id: str,
        status: str,
        pr_url: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update task status and optional fields.

        Args:
            task_id: Task ID
            status: New status (created, running, completed, failed, aborted)
            pr_url: PR URL (for completed tasks)
            error_message: Error message (for failed tasks)

        Returns:
            True if updated, False if task not found
        """
        from db import write_connect

        async with write_connect() as db:
            current_cur = await db.execute(
                "SELECT status FROM agent_tasks WHERE task_id = ?",
                (task_id,),
            )
            current_row = await current_cur.fetchone()
            if current_row is None:
                return False
            current = current_row[0]
            if current != status and status not in _ALLOWED_TRANSITIONS.get(current, set()):
                log.warning(
                    "TaskStore: rejected task transition %s -> %s for %s",
                    current,
                    status,
                    task_id,
                )
                return False

            cur = await db.execute(
                """
                UPDATE agent_tasks
                SET status = ?,
                    pr_url = COALESCE(?, pr_url),
                    error_message = CASE
                        WHEN ? IN ('failed', 'stuck', 'stuck_permanently')
                        THEN COALESCE(?, error_message)
                        ELSE NULL
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (status, pr_url, status, error_message, task_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def update_pr_url(self, task_id: str, pr_url: str) -> bool:
        """Persist a verified PR URL without changing lifecycle status."""
        from db import write_connect

        async with write_connect() as db:
            cur = await db.execute(
                """UPDATE agent_tasks
                   SET pr_url = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE task_id = ?""",
                (pr_url, task_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def claim_retry(
        self,
        task_id: str,
        allowed_statuses: frozenset[str],
        *,
        automatic: bool = False,
        stale_after_seconds: int = _RETRY_CLAIM_TTL_SECONDS,
    ) -> Optional[RetryClaim]:
        """Atomically lease an eligible task retry to one tokenized owner."""
        if not allowed_statuses:
            raise ValueError("allowed_statuses must not be empty")
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must not be negative")
        from db import write_connect

        async with write_connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            recovered_stale_claim = False
            existing_cur = await db.execute(
                """SELECT token,
                          claimed_at <= datetime('now', ?) AS is_stale
                   FROM agent_task_retry_claims WHERE task_id = ?""",
                (f"-{stale_after_seconds} seconds", task_id),
            )
            existing = await existing_cur.fetchone()
            if existing is not None:
                if not existing[1]:
                    await db.rollback()
                    return None
                await db.execute(
                    "DELETE FROM agent_task_retry_claims WHERE task_id = ? AND token = ?",
                    (task_id, existing[0]),
                )
                recovered_stale_claim = True
                await db.execute(
                    """UPDATE agent_tasks
                       SET status = 'stuck',
                           error_message = 'Recovered stale retry claim',
                           updated_at = CURRENT_TIMESTAMP
                       WHERE task_id = ? AND status = 'retrying'""",
                    (task_id,),
                )

            current_cur = await db.execute(
                "SELECT status FROM agent_tasks WHERE task_id = ?",
                (task_id,),
            )
            current_row = await current_cur.fetchone()
            if current_row is None or current_row[0] not in allowed_statuses:
                if recovered_stale_claim:
                    await db.commit()
                else:
                    await db.rollback()
                return None

            previous_status = current_row[0]
            token = uuid.uuid4().hex
            await db.execute(
                """INSERT INTO agent_task_retry_claims
                   (task_id, token, previous_status, automatic)
                   VALUES (?, ?, ?, ?)""",
                (task_id, token, previous_status, int(automatic)),
            )
            cur = await db.execute(
                """UPDATE agent_tasks
                   SET status = 'retrying', error_message = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE task_id = ? AND status = ?""",
                (task_id, previous_status),
            )
            if cur.rowcount != 1:
                await db.rollback()
                return None
            await db.commit()
            return RetryClaim(token=token, previous_status=previous_status)

    async def complete_retry_claim(self, task_id: str, token: str) -> bool:
        """Finalize retrying -> restarted only for the current lease owner."""
        from db import write_connect

        async with write_connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            owner_cur = await db.execute(
                "SELECT 1 FROM agent_task_retry_claims WHERE task_id = ? AND token = ?",
                (task_id, token),
            )
            if await owner_cur.fetchone() is None:
                await db.rollback()
                return False
            cur = await db.execute(
                """UPDATE agent_tasks
                   SET status = 'restarted', error_message = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE task_id = ? AND status = 'retrying'""",
                (task_id,),
            )
            if cur.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                "DELETE FROM agent_task_retry_claims WHERE task_id = ? AND token = ?",
                (task_id, token),
            )
            await db.commit()
            return True

    async def restore_retry_claim(
        self, task_id: str, token: str, status: str, error_message: str
    ) -> bool:
        """Restore a failed retry only while this caller still owns the claim."""
        if status not in {
            "running", "dispatched", "paused", "stuck", "failed", "aborted",
            "stuck_permanently",
        }:
            raise ValueError(f"Invalid retry fallback status: {status}")
        from db import write_connect

        persisted_error = (
            error_message[:2000]
            if status in {"stuck", "failed", "stuck_permanently"}
            else None
        )
        async with write_connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            owner_cur = await db.execute(
                "SELECT 1 FROM agent_task_retry_claims WHERE task_id = ? AND token = ?",
                (task_id, token),
            )
            if await owner_cur.fetchone() is None:
                await db.rollback()
                return False
            cur = await db.execute(
                """UPDATE agent_tasks
                   SET status = ?, error_message = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE task_id = ? AND status = 'retrying'""",
                (status, persisted_error, task_id),
            )
            if cur.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                "DELETE FROM agent_task_retry_claims WHERE task_id = ? AND token = ?",
                (task_id, token),
            )
            await db.commit()
            return True

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task record.

        Returns:
            True if deleted, False if task not found
        """
        from db import write_connect

        async with write_connect() as db:
            cur = await db.execute(
                "DELETE FROM agent_tasks WHERE task_id = ?",
                (task_id,),
            )
            await db.commit()
            return cur.rowcount > 0

    async def reserve_request(
        self, idempotency_key: str, request_hash: str, task_id: str
    ) -> dict:
        """Atomically reserve a task ID or return the prior request outcome."""
        from db import write_connect

        async with write_connect() as db:
            insert = await db.execute(
                """INSERT INTO agent_task_requests
                   (idempotency_key, request_hash, task_id)
                   VALUES (?, ?, ?)
                   ON CONFLICT(idempotency_key) DO NOTHING""",
                (idempotency_key, request_hash, task_id),
            )
            if insert.rowcount == 1:
                await db.commit()
                return {"owner": True, "task_id": task_id, "state": "pending"}

            cur = await db.execute(
                """SELECT request_hash, task_id, state, error_message
                   FROM agent_task_requests WHERE idempotency_key = ?""",
                (idempotency_key,),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuntimeError("Idempotency reservation conflict was not recoverable")

            existing_hash, existing_task_id, state, error = row
            if existing_hash != request_hash:
                raise IdempotencyConflict(
                    "Idempotency-Key was already used with a different request"
                )

            # Recover a completion-marker write lost after successful dispatch.
            if state == "pending":
                task_cur = await db.execute(
                    "SELECT status FROM agent_tasks WHERE task_id = ?",
                    (existing_task_id,),
                )
                task_row = await task_cur.fetchone()
                if task_row and task_row[0] != "created":
                    state = "completed"
                    await db.execute(
                        """UPDATE agent_task_requests
                           SET state = 'completed', updated_at = CURRENT_TIMESTAMP
                           WHERE idempotency_key = ?""",
                        (idempotency_key,),
                    )
                    await db.commit()

            return {
                "owner": False,
                "task_id": existing_task_id,
                "state": state,
                "error_message": error,
            }

    async def complete_request(self, idempotency_key: str) -> None:
        from db import write_connect

        async with write_connect() as db:
            cur = await db.execute(
                """UPDATE agent_task_requests
                   SET state = 'completed', error_message = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE idempotency_key = ? AND state = 'pending'""",
                (idempotency_key,),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Idempotency request is not pending")
            await db.commit()

    async def fail_request(self, idempotency_key: str, error: str) -> None:
        from db import write_connect

        async with write_connect() as db:
            cur = await db.execute(
                """UPDATE agent_task_requests
                   SET state = 'failed', error_message = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE idempotency_key = ? AND state = 'pending'""",
                (error[:2000], idempotency_key),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Idempotency request is not pending")
            await db.commit()


class TaskStateProjector:
    """Serialize dashboard lifecycle events into the durable task registry."""

    def __init__(
        self,
        store: TaskStore,
        *,
        max_pending_events: int = 4096,
        retry_base_delay: float = 0.1,
        retry_max_delay: float = 5.0,
        flush_timeout: float = 30.0,
        shutdown_drain_timeout: float = 2.0,
    ):
        if max_pending_events < 1:
            raise ValueError("max_pending_events must be positive")
        self._store = store
        self._max_pending_events = max_pending_events
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._flush_timeout = flush_timeout
        self._shutdown_drain_timeout = shutdown_drain_timeout
        self._pending: OrderedDict[tuple[str, str], tuple] = OrderedDict()
        self._last_status: dict[str, str] = {}
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._inflight: tuple | None = None
        self._dropped_events = 0

    @property
    def backlog_size(self) -> int:
        return len(self._pending) + (1 if self._inflight is not None else 0)

    @property
    def dropped_events(self) -> int:
        return self._dropped_events

    @staticmethod
    def _is_critical(item: tuple) -> bool:
        kind, _, value, _ = item
        return kind == "pr_url" or value in _PERSISTED_TERMINAL_STATUSES

    def _discard_oldest_noncritical(self) -> bool:
        for key, item in list(self._pending.items()):
            if self._is_critical(item):
                continue
            self._pending.pop(key)
            if item[0] == "status" and self._last_status.get(item[1]) == item[2]:
                self._last_status.pop(item[1], None)
            self._dropped_events += 1
            log.error(
                "TaskStateProjector: evicted non-terminal event for task %s; "
                "backlog capacity=%d",
                item[1],
                self._max_pending_events,
            )
            return True
        return False

    def _enqueue(self, item: tuple) -> bool:
        key = (item[0], item[1])
        if key in self._pending:
            self._pending[key] = item
            self._pending.move_to_end(key)
        else:
            if len(self._pending) >= self._max_pending_events:
                if not self._discard_oldest_noncritical():
                    self._dropped_events += 1
                    log.critical(
                        "TaskStateProjector: rejected critical event for task %s; "
                        "backlog contains only critical events (capacity=%d)",
                        item[1],
                        self._max_pending_events,
                    )
                    return False
            self._pending[key] = item
        self._idle.clear()
        self._wake.set()
        return True

    def enqueue_status(
        self, task_id: str, status: str, error_message: Optional[str] = None
    ) -> bool:
        if not task_id or self._last_status.get(task_id) == status:
            return bool(task_id)
        accepted = self._enqueue(("status", task_id, status, error_message))
        if accepted:
            self._last_status[task_id] = status
        return accepted

    def enqueue_pr_url(self, task_id: str, pr_url: str) -> bool:
        if task_id and pr_url:
            return self._enqueue(("pr_url", task_id, pr_url, None))
        return False

    async def _apply(self, item: tuple) -> None:
        kind, task_id, value, error = item
        if kind == "status":
            updated = await self._store.update_status(
                task_id, value, error_message=error
            )
        else:
            updated = await self._store.update_pr_url(task_id, value)
        if not updated:
            if kind == "status" and self._last_status.get(task_id) == value:
                self._last_status.pop(task_id, None)
            log.warning(
                "TaskStateProjector: rejected %s update for task %s",
                kind,
                task_id,
            )
        elif (
            kind == "status"
            and value in _PERSISTED_TERMINAL_STATUSES
            and self._last_status.get(task_id) == value
        ):
            self._last_status.pop(task_id, None)

    async def run(self) -> None:
        """Consume projections with bounded coalescing and exponential retry."""
        current_key = None
        try:
            while True:
                await self._wake.wait()
                while self._pending:
                    current_key, self._inflight = self._pending.popitem(last=False)
                    attempt = 0
                    while True:
                        try:
                            await self._apply(self._inflight)
                            break
                        except Exception:
                            attempt += 1
                            if attempt == 1 or attempt & (attempt - 1) == 0:
                                log.exception(
                                    "TaskStateProjector: failed to persist task %s "
                                    "(attempt %d)",
                                    self._inflight[1],
                                    attempt,
                                )
                            delay = min(
                                self._retry_base_delay * (2 ** min(attempt - 1, 16)),
                                self._retry_max_delay,
                            )
                            await asyncio.sleep(random.uniform(delay / 2, delay))
                    current_key = None
                    self._inflight = None

                self._wake.clear()
                self._idle.set()
        except asyncio.CancelledError:
            if current_key is not None and current_key not in self._pending:
                self._pending[current_key] = self._inflight
                self._pending.move_to_end(current_key, last=False)
            self._inflight = None
            try:
                async with asyncio.timeout(self._shutdown_drain_timeout):
                    while self._pending:
                        _, item = self._pending.popitem(last=False)
                        try:
                            await self._apply(item)
                        except Exception:
                            log.exception(
                                "TaskStateProjector: failed to drain task %s", item[1]
                            )
            except TimeoutError:
                log.error(
                    "TaskStateProjector: shutdown drain timed out with %d events",
                    len(self._pending),
                )
            finally:
                self._pending.clear()
                self._idle.set()
            raise

    async def flush(self, timeout: float | None = None) -> None:
        """Wait until all events queued before this call have been persisted."""
        await asyncio.wait_for(
            self._idle.wait(),
            timeout=self._flush_timeout if timeout is None else timeout,
        )
