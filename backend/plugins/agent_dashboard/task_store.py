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
    "stuck": {"retrying", "restarted", "failed", "aborted", "stuck_permanently"},
    "retrying": {"running", "restarted", "failed", "aborted", "stuck_permanently"},
    "restarted": {
        "running", "paused", "stuck", "retrying", "completed", "failed", "aborted",
    },
    "failed": {"restarted", "aborted"},
    "aborted": {"restarted"},
    "stuck_permanently": {"restarted", "aborted"},
    "completed": set(),
}
_PERSISTED_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "aborted", "stuck_permanently"}
)


class IdempotencyConflict(RuntimeError):
    pass


class TaskCreationInProgress(RuntimeError):
    pass


class PreviousTaskCreationFailed(RuntimeError):
    pass


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

    def __init__(self, store: TaskStore):
        self._store = store
        self._queue: asyncio.Queue = asyncio.Queue()
        self._last_status: dict[str, str] = {}

    def enqueue_status(
        self, task_id: str, status: str, error_message: Optional[str] = None
    ) -> None:
        if not task_id or self._last_status.get(task_id) == status:
            return
        self._last_status[task_id] = status
        self._queue.put_nowait(("status", task_id, status, error_message))

    def enqueue_pr_url(self, task_id: str, pr_url: str) -> None:
        if task_id and pr_url:
            self._queue.put_nowait(("pr_url", task_id, pr_url, None))

    async def _apply(self, item: tuple) -> None:
        kind, task_id, value, error = item
        if kind == "status":
            updated = await self._store.update_status(
                task_id, value, error_message=error
            )
        else:
            updated = await self._store.update_pr_url(task_id, value)
        if not updated:
            if kind == "status":
                self._last_status.pop(task_id, None)
            log.warning(
                "TaskStateProjector: rejected %s update for task %s",
                kind,
                task_id,
            )
        elif kind == "status" and value in _PERSISTED_TERMINAL_STATUSES:
            self._last_status.pop(task_id, None)

    async def run(self) -> None:
        """Consume projections until cancelled, draining queued terminal events."""
        try:
            while True:
                item = await self._queue.get()
                try:
                    await self._apply(item)
                except Exception:
                    log.exception(
                        "TaskStateProjector: failed to persist task %s", item[1]
                    )
                    # Retry transient database failures. The task state machine
                    # prevents a delayed event from regressing a terminal state.
                    self._queue.put_nowait(item)
                    await asyncio.sleep(0.1)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            while not self._queue.empty():
                item = self._queue.get_nowait()
                try:
                    await self._apply(item)
                except Exception:
                    log.exception(
                        "TaskStateProjector: failed to drain task %s", item[1]
                    )
                finally:
                    self._queue.task_done()
            raise

    async def flush(self) -> None:
        """Wait until all events queued before this call have been persisted."""
        await self._queue.join()
