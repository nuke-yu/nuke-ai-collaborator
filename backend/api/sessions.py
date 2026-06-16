from fastapi import APIRouter, HTTPException, Depends
import db
from sessions import resume_session, update_session_status, get_session, get_group_sessions, get_events
from core.orchestration.locks import release_lock
from runtime.dbpaths import group_db_path
from api.deps import ensure_group_ready

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

@router.get("/group/{group_id}", dependencies=[Depends(ensure_group_ready)])
async def api_get_group_sessions(group_id: int):
    try:
        with db.bind_db(group_db_path(group_id)):
            return await get_group_sessions(group_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{session_id}", dependencies=[Depends(ensure_group_ready)])
async def api_get_session(session_id: str, group_id: int | None = None):
    if not group_id:
        raise HTTPException(status_code=400, detail="Missing group_id query parameter")
    with db.bind_db(group_db_path(group_id)):
        session = await get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        return session

@router.get("/{session_id}/events", dependencies=[Depends(ensure_group_ready)])
async def api_get_session_events(session_id: str, group_id: int | None = None):
    if not group_id:
        raise HTTPException(status_code=400, detail="Missing group_id query parameter")
    try:
        with db.bind_db(group_db_path(group_id)):
            return await get_events(session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{session_id}/resume", dependencies=[Depends(ensure_group_ready)])
async def api_resume_session(session_id: str, group_id: int | None = None):
    if not group_id:
        raise HTTPException(status_code=400, detail="Missing group_id query parameter")
    with db.bind_db(group_db_path(group_id)):
        success = await resume_session(session_id)
        if not success:
            raise HTTPException(status_code=400, detail="无法恢复会话。会话不存在或状态不正确。")
        return {"status": "ok", "message": "会话恢复指令已发出"}

@router.post("/{session_id}/cancel-recovery", dependencies=[Depends(ensure_group_ready)])
async def api_cancel_recovery(session_id: str, group_id: int | None = None):
    if not group_id:
        raise HTTPException(status_code=400, detail="Missing group_id query parameter")
    with db.bind_db(group_db_path(group_id)):
        session = await get_session(session_id)
        if session:
            await release_lock(session["group_id"])
        # Simply mark the session as failed so it won't be prompted again
        await update_session_status(session_id, "failed")
        return {"status": "ok", "message": "会话恢复已取消，已释放群组锁"}

