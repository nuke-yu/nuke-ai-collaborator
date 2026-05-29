"""scheduler/router.py — FastAPI endpoints for managing cron jobs."""
from fastapi import APIRouter, HTTPException
import scheduler.engine as engine
from scheduler.store import list_jobs, get_job, create_job, update_job, delete_job, toggle_job

router = APIRouter(prefix="/api/cron-jobs", tags=["scheduler"])


def _404(job_id: int):
    raise HTTPException(404, f"Cron job {job_id} not found")


@router.get("")
async def api_list_jobs(bot_id: int | None = None, group_id: int | None = None):
    return {"jobs": await list_jobs(bot_id=bot_id, group_id=group_id)}


@router.post("")
async def api_create_job(body: dict):
    bot_id    = body.get("bot_id")
    group_id  = body.get("group_id")
    cron_expr = (body.get("cron_expr") or "").strip()
    message   = (body.get("message") or "").strip()
    if not all([bot_id, group_id, cron_expr, message]):
        raise HTTPException(400, "bot_id, group_id, cron_expr, message are required")
    if not engine.validate_cron_expr(cron_expr):
        raise HTTPException(400, f"Invalid cron expression: {cron_expr!r}")
    job = await create_job(
        bot_id=int(bot_id), group_id=int(group_id),
        cron_expr=cron_expr, message=message,
        label=body.get("label", ""),
        enabled=bool(body.get("enabled", True)),
    )
    engine.reload_job(job)
    return job


@router.get("/{job_id}")
async def api_get_job(job_id: int):
    job = await get_job(job_id)
    if not job:
        _404(job_id)
    return job


@router.put("/{job_id}")
async def api_update_job(job_id: int, body: dict):
    job = await get_job(job_id)
    if not job:
        _404(job_id)
    if "cron_expr" in body and not engine.validate_cron_expr(body["cron_expr"]):
        raise HTTPException(400, f"Invalid cron expression: {body['cron_expr']!r}")
    job = await update_job(job_id, **body)
    engine.reload_job(job)
    return job


@router.delete("/{job_id}")
async def api_delete_job(job_id: int):
    job = await get_job(job_id)
    if not job:
        _404(job_id)
    engine.reload_job({**job, "enabled": False})   # pull from scheduler before DB delete
    await delete_job(job_id)
    return {"ok": True}


@router.post("/{job_id}/toggle")
async def api_toggle_job(job_id: int):
    job = await get_job(job_id)
    if not job:
        _404(job_id)
    job = await toggle_job(job_id)
    engine.reload_job(job)
    return job


@router.post("/{job_id}/run")
async def api_run_now(job_id: int):
    job = await get_job(job_id)
    if not job:
        _404(job_id)
    await engine.run_now(job)
    return {"ok": True}
