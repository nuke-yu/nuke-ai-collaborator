"""scheduler/engine.py — APScheduler wrapper.

Owns the in-process AsyncIOScheduler instance. Knows nothing about HTTP or DB
schema beyond what store.py returns; runner.py holds all app coupling.
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.store import list_jobs
from scheduler.runner import configure_wake_dispatch, fire_job

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _apscheduler_job_id(db_id: int) -> str:
    return f"cron_job_{db_id}"


def _parse_cron(expr: str) -> CronTrigger | None:
    """Parse a 5-field cron expression; returns None on invalid input."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return None
    minute, hour, day, month, day_of_week = parts
    try:
        # DFT-040: pin to UTC so cron fields are interpreted consistently
        # regardless of host local timezone / DST shifts. A pre-built trigger
        # keeps its own tz when added to the scheduler, so this must be set here.
        return CronTrigger(
            minute=minute, hour=hour, day=day,
            month=month, day_of_week=day_of_week, timezone="UTC",
        )
    except Exception:
        return None


def validate_cron_expr(expr: str) -> bool:
    """Return True if expr is a valid 5-field cron expression."""
    return _parse_cron(expr) is not None


async def start() -> None:
    """Load all enabled jobs from DB and start the scheduler. Called at app startup."""
    global _scheduler
    # DFT-040: run the scheduler in UTC; cron_jobs.created_at is stored UTC too,
    # so all time handling stays in one timezone (frontend converts to local).
    _scheduler = AsyncIOScheduler(timezone="UTC")
    jobs = await list_jobs(enabled_only=True)
    for job in jobs:
        _schedule(job)
    _scheduler.start()
    log.info("scheduler started with %d active job(s)", len(jobs))


def stop() -> None:
    """Graceful shutdown. Called at app teardown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
    configure_wake_dispatch(None)
    log.info("scheduler stopped")


def _schedule(job: dict) -> None:
    if _scheduler is None:
        return
    trigger = _parse_cron(job["cron_expr"])
    if trigger is None:
        log.warning("scheduler: skipping job id=%d, invalid cron_expr=%r", job["id"], job["cron_expr"])
        return
    _scheduler.add_job(
        fire_job,
        trigger,
        args=[job["bot_id"], job["group_id"], job["message"], job["id"]],
        id=_apscheduler_job_id(job["id"]),
        replace_existing=True,
        misfire_grace_time=3600, # DFT-039: allow 1h window for catch-up after restart
        coalesce=True,
    )


def _unschedule(job_id: int) -> None:
    if _scheduler is None:
        return
    jid = _apscheduler_job_id(job_id)
    if _scheduler.get_job(jid):
        _scheduler.remove_job(jid)


def reload_job(job: dict) -> None:
    """Sync a single job into the live scheduler after a DB change."""
    _unschedule(job["id"])
    if job.get("enabled"):
        _schedule(job)


async def run_now(job: dict) -> None:
    """Immediate one-off execution, bypasses the cron schedule."""
    await fire_job(job["bot_id"], job["group_id"], job["message"])
