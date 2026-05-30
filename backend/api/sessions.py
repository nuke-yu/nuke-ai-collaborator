from fastapi import APIRouter, HTTPException
from sessions import resume_session, update_session_status, get_session
from core.orchestration.locks import release_lock

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

@router.post("/{session_id}/resume")
async def api_resume_session(session_id: str):
    success = await resume_session(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法恢复会话。会话不存在或状态不正确。")
    return {"status": "ok", "message": "会话恢复指令已发出"}

@router.post("/{session_id}/cancel-recovery")
async def api_cancel_recovery(session_id: str):
    session = await get_session(session_id)
    if session:
        await release_lock(session["group_id"])
    # Simply mark the session as failed so it won't be prompted again
    await update_session_status(session_id, "failed")
    return {"status": "ok", "message": "会话恢复已取消，已释放群组锁"}
