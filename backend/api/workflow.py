from fastapi import APIRouter, HTTPException
from db import get_db, get_members
from ws_manager import manager
import core.workflow as wf
from core import workflow_store

router = APIRouter()


@router.get("/api/groups/{group_id}/workflow")
async def get_workflow(group_id: int):
    return wf._snapshot(group_id)


@router.post("/api/groups/{group_id}/workflow/start")
async def start_workflow(group_id: int, body: dict):
    async with get_db() as db:
        members = await get_members(db, group_id)
    bots = {m["id"]: m for m in members if m["type"] == "bot"}

    orchestrator_id = body.get("orchestrator_id", "workflow_v1")
    if orchestrator_id != "workflow_v1":
        # 非内置编排器：spec 形状由各编排器自定，这里只做通用的 bot_id → 成员解析。
        spec = dict(body.get("spec", {}))
        if "bots" in spec:
            spec["bots"] = [bots[bid] for bid in spec["bots"] if bid in bots]
        if not spec.get("bots"):
            raise HTTPException(400, "No bots specified")
        await wf.apply(group_id, wf.start(group_id, spec, orchestrator_id))
        return {"ok": True}

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
    await wf.apply(group_id, wf.start(group_id, ordered))
    return {"ok": True}


@router.post("/api/groups/{group_id}/workflow/next")
async def next_workflow(group_id: int):
    await wf.advance(group_id)
    return {"ok": True}


@router.delete("/api/groups/{group_id}/workflow")
async def end_workflow(group_id: int):
    wf.end(group_id)
    await workflow_store.clear_state(group_id)
    await manager.broadcast(group_id, {"type": "workflow_update", "active": False})
    return {"ok": True}
