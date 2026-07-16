"""Durable lifecycle reconciliation for coding-agent tasks."""

import asyncio
import logging
import os
from pathlib import Path

import db
from runtime.dbpaths import group_db_path

log = logging.getLogger(__name__)

RECONCILE_INTERVAL_SEC = 60
TASK_START_GRACE_SEC = 180
NEEDS_REVIEW_TTL_SEC = 600 if os.getenv("NUKE_ENV", "").lower() != "production" else 86400
_TASK_TERMINAL = frozenset({"completed", "failed", "aborted", "stuck_permanently"})
_SESSION_ACTIVE = ("running", "recovering", "awaiting_recovery", "needs_review")


async def finalize_group_state(group_id: int, bot_id: int, status: str = "aborted") -> None:
    """Close non-terminal sessions and workflow state for one coding task."""
    path = group_db_path(group_id)
    if Path(path).exists():
        placeholders = ",".join("?" for _ in _SESSION_ACTIVE)
        async with db.write_connect(path) as conn:
            await conn.execute(
                f"""UPDATE agent_sessions SET status = ?, updated_at = datetime('now')
                    WHERE group_id = ? AND bot_id = ?
                      AND status IN ({placeholders})""",
                (status, group_id, bot_id, *_SESSION_ACTIVE),
            )
            await conn.execute("DELETE FROM workflow_state WHERE group_id = ?", (group_id,))
            await conn.commit()

    # Coding-agent groups are dedicated execution containers. Once terminal,
    # retain their history but release the persistent worker pin so future
    # process starts do not hydrate an empty project group.
    async with db.write_connect(db.DB_PATH) as conn:
        await conn.execute(
            "UPDATE groups SET assigned_worker_id = NULL WHERE id = ?",
            (group_id,),
        )
        await conn.commit()


class TaskReconciler:
    """Converge database state after crashes and periodically reap stale work."""

    def __init__(self, task_store, interval: int = RECONCILE_INTERVAL_SEC):
        self._store = task_store
        self._interval = interval
        self._running = False

    async def run(self) -> None:
        self._running = True
        await self.run_once(startup=True)
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("TaskReconciler: periodic reconciliation failed")

    def stop(self) -> None:
        self._running = False

    async def run_once(self, *, startup: bool = False) -> None:
        if startup:
            result = await self._store.reconcile_transient_states()
            if any(result.values()):
                log.warning("TaskReconciler: startup state repair %s", result)

        for task in await self._store.list_tasks(limit=1000):
            try:
                await self._reconcile_task(task)
            except Exception:
                log.exception("TaskReconciler: failed task %s", task["task_id"])

    async def _reconcile_task(self, task: dict) -> None:
        group_id = task["group_id"]
        bot_id = task["bot_id"]
        if task["status"] in _TASK_TERMINAL:
            await finalize_group_state(group_id, bot_id)
            return

        path = group_db_path(group_id)
        if not Path(path).exists():
            return

        placeholders = ",".join("?" for _ in _SESSION_ACTIVE)
        async with db.write_connect(path) as conn:
            cur = await conn.execute(
                f"""SELECT id, status,
                           (julianday('now') - julianday(updated_at)) * 86400 AS age
                    FROM agent_sessions
                    WHERE group_id = ? AND bot_id = ?
                      AND status IN ({placeholders})
                    ORDER BY updated_at DESC, created_at DESC""",
                (group_id, bot_id, *_SESSION_ACTIVE),
            )
            rows = await cur.fetchall()

            # A retry may leave several sessions marked running. Only the newest
            # execution can own the task; older copies must never be recovered.
            live = [row for row in rows if row[1] in {"running", "recovering"}]
            for duplicate in live[1:]:
                await conn.execute(
                    "UPDATE agent_sessions SET status = 'superseded', updated_at = datetime('now') WHERE id = ?",
                    (duplicate[0],),
                )

            expired_review = False
            for row in rows:
                if row[1] == "needs_review" and (row[2] or 0) >= NEEDS_REVIEW_TTL_SEC:
                    expired_review = True
                    await conn.execute(
                        "UPDATE agent_sessions SET status = 'expired', updated_at = datetime('now') WHERE id = ?",
                        (row[0],),
                    )
            await conn.commit()

        if live:
            return

        pending_review = any(
            row[1] == "needs_review" and (row[2] or 0) < NEEDS_REVIEW_TTL_SEC
            for row in rows
        )
        if pending_review:
            await self._store.update_status(
                task["task_id"], "stuck", error_message="Session requires human review"
            )
        elif expired_review:
            await self._store.update_status(
                task["task_id"], "failed", error_message="Session review expired"
            )
            await finalize_group_state(group_id, bot_id, status="expired")
