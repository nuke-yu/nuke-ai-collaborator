"""scheduler/runner.py — sole coupling point between scheduler and the main app.

Only this file may import from the application boundary. It emits a typed IPC
wake frame through the Supervisor callback; it never imports Worker workflow
state or mutates a group database directly.
"""
import logging
from collections.abc import Awaitable, Callable

from runtime import ipc

log = logging.getLogger(__name__)

_SYSTEM_SENDER = {"id": 0, "name": "系统调度器", "type": "system", "avatar_color": "#6b7280"}

# Installed by the Supervisor composition root.  The scheduler must not call
# workflow code directly: workflow state belongs to the Worker that owns the
# group's lease.  The callback is deliberately the narrow Supervisor routing
# contract rather than a scheduler dependency on Worker internals.
_wake_dispatch: Callable[[int, dict], Awaitable[None]] | None = None


def configure_wake_dispatch(dispatch: Callable[[int, dict], Awaitable[None]] | None) -> None:
    """Install or clear the Supervisor-backed wake-frame dispatcher."""
    global _wake_dispatch
    _wake_dispatch = dispatch


async def fire_job(bot_id: int, group_id: int, message: str, job_id: int | None = None) -> None:
    # Lazy import keeps the scheduler importable even if orchestrator fails to load
    from db import get_db, get_members, get_messages
    from scheduler.store import update_last_run

    if job_id:
        try:
            await update_last_run(job_id)
        except Exception:
            log.exception("scheduler: failed to update last_run_at for job %d", job_id)

    try:
        async with get_db() as db:
            all_members = await get_members(db, group_id)
            recent = await get_messages(db, group_id)
    except Exception as exc:
        log.error("scheduler: DB read failed for bot_id=%d group_id=%d: %s", bot_id, group_id, exc)
        return

    all_bots = [m for m in all_members if m["type"] == "bot"]
    bot = next((b for b in all_bots if b["id"] == bot_id), None)
    if not bot:
        log.warning("scheduler: bot_id=%d not found in group_id=%d — skipping", bot_id, group_id)
        return

    if _wake_dispatch is None:
        log.error(
            "scheduler: no Supervisor wake dispatcher configured; refusing to "
            "claim Cron job bot_id=%d group_id=%d",
            bot_id, group_id,
        )
        return

    try:
        await _wake_dispatch(
            group_id,
            ipc.protocol.envelope(
                ipc.protocol.WAKE_TRIGGER,
                group_id=group_id,
                bot_id=bot_id,
                content=message,
                trace_id=f"cron:{job_id}" if job_id else None,
            ),
        )
        log.info("scheduler: fired job bot_id=%d group_id=%d msg=%r", bot_id, group_id, message[:60])
    except Exception as exc:
        log.error("scheduler: dispatch failed bot_id=%d group_id=%d: %s", bot_id, group_id, exc)
