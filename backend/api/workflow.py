from fastapi import APIRouter, HTTPException
import db
from db import get_db, get_members
from ws_manager import manager
import core.workflow as wf
from core import workflow_store
from core.orchestration import registry as orch_registry
from runtime.dbpaths import group_db_path
from runtime import supervisor as sup_mod
from runtime import ipc

router = APIRouter()


@router.get("/api/groups/{group_id}/workflow")
async def get_workflow(group_id: int):
    # 编排实际跑在 worker 进程；REST 应用在主进程、其内存编排器恒空，wf._snapshot()
    # 在这里永远报 inactive。改为从 group 私有库读持久化快照（每次阶段/门变化都会
    # broadcast_state 落库），前端加载后再靠 live workflow_update 事件保持实时。
    with db.bind_db(group_db_path(group_id)):
        rows = await workflow_store.load_all_active(group_id=group_id)
    if not rows:
        return {"active": False}
    row = rows[0]
    orch = orch_registry.get(row.get("orchestrator_id") or "workflow_v1")
    return orch.snapshot_state(row["state"])


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
    # 编排器活内存状态在 worker 进程；主进程直接 wf.advance 是操作空内存的 no-op。
    # 转发控制帧给 worker 执行（同 confirm 路径）。
    await sup_mod.supervisor.send_to_worker(
        group_id, ipc.protocol.envelope(ipc.protocol.WORKFLOW_NEXT, group_id=group_id))
    return {"ok": True}


@router.delete("/api/groups/{group_id}/workflow")
async def end_workflow(group_id: int):
    # 主进程 wf.end 是空内存 no-op、clear_state 还会写错库（未 bind 群库）。
    # 转发给 worker：由它丢内存编排状态、清持久化、广播 workflow_update。
    await sup_mod.supervisor.send_to_worker(
        group_id, ipc.protocol.envelope(ipc.protocol.WORKFLOW_END, group_id=group_id))
    return {"ok": True}
