"""Durable, group-isolated execution run lifecycle records."""
from __future__ import annotations

import time

from memory.infrastructure import SQLiteMemoryDatabase

def _database() -> SQLiteMemoryDatabase:
    from memory.canonical import _runtime_composition
    return _runtime_composition().database


async def start_run(
    *, run_id: str, group_id: int | None, bot_id: int | None,
    session_id: str, thread_id: str | None, provider: str,
    model: str, executor: str,
) -> None:
    if group_id is None or not run_id:
        return
    now = int(time.time() * 1000)
    async with await _database().connect("agent_runs", group_id, write=True) as db:
        await db.execute(
            "INSERT INTO agent_runs "
            "(run_id, group_id, bot_id, thread_id, session_id, status, provider, model, "
            " executor, started_at, updated_at) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET status='running', completed_at=NULL, "
            "error_summary='', updated_at=excluded.updated_at",
            (run_id, group_id, bot_id, thread_id or "", session_id, provider, model, executor, now, now),
        )
        await db.commit()


async def finish_run(
    *, run_id: str, group_id: int | None, status: str,
    iterations: int = 0, input_tokens: int = 0, output_tokens: int = 0,
    error_summary: str = "",
) -> None:
    if group_id is None or not run_id:
        return
    if status not in {"completed", "failed", "cancelled", "abandoned"}:
        raise ValueError(f"invalid terminal run status: {status}")
    now = int(time.time() * 1000)
    async with await _database().connect("agent_runs", group_id, write=True) as db:
        await db.execute(
            "UPDATE agent_runs SET status=?, completed_at=?, iterations=?, input_tokens=?, "
            "output_tokens=?, error_summary=?, updated_at=? WHERE run_id=? AND group_id=?",
            (status, now, iterations, input_tokens, output_tokens, error_summary[:2000], now, run_id, group_id),
        )
        await db.commit()


async def touch_run(*, run_id: str, group_id: int | None) -> None:
    """Heartbeat update to prevent active long-running agent tasks from timing out."""
    if group_id is None or not run_id:
        return
    now = int(time.time() * 1000)
    async with await _database().connect("agent_runs", group_id, write=True) as db:
        await db.execute(
            "UPDATE agent_runs SET updated_at=? WHERE run_id=? AND group_id=? AND status='running'",
            (now, run_id, group_id),
        )
        await db.commit()


async def recover_abandoned_runs(
    group_id: int, *, timeout_seconds: int = 600
) -> int:
    """Recover stale running runs left behind after worker crashes."""
    if group_id <= 0:
        return 0
    now = int(time.time() * 1000)
    cutoff = now - (timeout_seconds * 1000)

    async with await _database().connect("agent_runs", group_id, write=True) as db:
        cursor = await db.execute(
            """UPDATE agent_runs
            SET status='abandoned', completed_at=?, error_summary='abandoned_stale_worker_timeout', updated_at=?
            WHERE group_id=? AND status='running' AND updated_at < ?""",
            (now, now, group_id, cutoff),
        )
        await db.commit()
        return cursor.rowcount
