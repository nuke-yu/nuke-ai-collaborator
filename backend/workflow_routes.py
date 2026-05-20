from fastapi import APIRouter, HTTPException
from database import get_db, get_members
from ws_manager import manager
import workflow as wf

router = APIRouter()


@router.get("/api/groups/{group_id}/workflow")
async def get_workflow(group_id: int):
    return wf._snapshot(group_id)


@router.post("/api/groups/{group_id}/workflow/start")
async def start_workflow(group_id: int, body: dict):
    async with get_db() as db:
        members = await get_members(db, group_id)
    bots = {m["id"]: m for m in members if m["type"] == "bot"}
    stages_cfg = body.get("stages", [])
    ordered = []
    for cfg in stages_cfg:
        if "pool" in cfg:
            pool_bots = [bots[b["bot_id"]] for b in cfg["pool"] if b["bot_id"] in bots]
            if pool_bots:
                ordered.append({
                    "stage_type": "pool", "bots": pool_bots,
                    "done_keyword": cfg.get("done_keyword", "完毕"),
                    "in_progress": {}, "completed_tickets": [],
                    "ticket_queue": [], "idle_bots": [],
                })
        else:
            bot = bots.get(cfg.get("bot_id"))
            if bot:
                ordered.append({**bot, "stage_type": "single", "done_keyword": cfg.get("done_keyword", "完毕")})
    if not ordered:
        raise HTTPException(400, "No bots specified")
    wf.start(group_id, ordered)
    await wf.broadcast_state(group_id)
    return {"ok": True}


@router.post("/api/groups/{group_id}/workflow/next")
async def next_workflow(group_id: int):
    await wf.advance(group_id)
    return {"ok": True}


@router.delete("/api/groups/{group_id}/workflow")
async def end_workflow(group_id: int):
    wf.end(group_id)
    await manager.broadcast(group_id, {"type": "workflow_update", "active": False})
    return {"ok": True}
