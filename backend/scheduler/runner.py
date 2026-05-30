"""scheduler/runner.py — sole coupling point between scheduler and the main app.

Only this file may import from core.* or db.*. All other scheduler files
depend only on each other and the standard library.
"""
import logging

log = logging.getLogger(__name__)

_SYSTEM_SENDER = {"id": 0, "name": "系统调度器", "type": "system", "avatar_color": "#6b7280"}


async def fire_job(bot_id: int, group_id: int, message: str, job_id: int | None = None) -> None:
    # Lazy import keeps the scheduler importable even if orchestrator fails to load
    from db import get_db, get_members, get_messages
    from core.orchestrator import dispatch_bots
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

    try:
        await dispatch_bots(
            group_id, [bot], message, _SYSTEM_SENDER,
            recent, all_bots, all_members,
        )
        log.info("scheduler: fired job bot_id=%d group_id=%d msg=%r", bot_id, group_id, message[:60])
    except Exception as exc:
        log.error("scheduler: dispatch failed bot_id=%d group_id=%d: %s", bot_id, group_id, exc)
