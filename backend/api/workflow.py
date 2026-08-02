from fastapi import APIRouter, HTTPException, Depends, Query
import db
from db import get_db, get_members
from api.deps import ensure_group_ready, require_group_member_ready
from ws_manager import manager
import core.workflow as wf
from core import workflow_store
from core.orchestration import registry as orch_registry
from runtime.dbpaths import group_db_path
from runtime import supervisor as sup_mod
from runtime import ipc
from observability.timeline import get_group_timeline
from observability.payload_policy import PayloadArtifactError, get_artifact

router = APIRouter()


@router.get("/api/groups/{group_id}/observability/artifacts/{artifact_id}")
async def get_observation_artifact(
    group_id: int,
    artifact_id: str,
    _user: dict = Depends(require_group_member_ready),
):
    try:
        with db.bind_db(group_db_path(group_id)):
            async with db.get_db() as conn:
                artifact = await get_artifact(conn, group_id, artifact_id)
    except PayloadArtifactError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get("/api/groups/{group_id}/timeline")
async def get_timeline(
    group_id: int,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    source: list[str] | None = Query(None),
    event_type: list[str] | None = Query(None),
    event_class: list[str] | None = Query(None),
    business_significant: bool | None = True,
    workflow_id: str | None = None,
    session_id: str | None = None,
    _user: dict = Depends(require_group_member_ready),
):
    try:
        with db.bind_db(group_db_path(group_id)):
            return await get_group_timeline(
                group_id,
                limit=limit,
                cursor=cursor,
                sources=source or (),
                event_types=event_type or (),
                event_classes=event_class or (),
                business_significant=business_significant,
                workflow_id=workflow_id,
                session_id=session_id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/groups/{group_id}/workflow", dependencies=[Depends(ensure_group_ready)])
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
    # Forward the start workflow command payload directly to the worker process
    # so that the workflow begins and schedules tasks inside the worker process context.
    await sup_mod.supervisor.send_to_worker(
        group_id, ipc.protocol.envelope(
            ipc.protocol.START_WORKFLOW, group_id=group_id, body=body
        )
    )
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
