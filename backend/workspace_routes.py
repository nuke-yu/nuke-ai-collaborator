from fastapi import APIRouter, HTTPException
from database import get_db, get_member
from workspace import (
    list_workspace_tree, read_file, write_file, bot_workspace, init_bot_workspace,
)
from skills import list_skills_all, update_skill_status, approve_draft_skill, reject_draft_skill

router = APIRouter()


@router.get("/api/members/{member_id}/workspace")
async def get_workspace_tree(member_id: int):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    return list_workspace_tree(member_id)


@router.get("/api/members/{member_id}/workspace/file")
async def get_workspace_file(member_id: int, path: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    content = await read_file(member_id, path)
    if content.startswith("[错误]"):
        raise HTTPException(400, content)
    return {"path": path, "content": content}


@router.put("/api/members/{member_id}/workspace/file")
async def put_workspace_file(member_id: int, body: dict):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    path = body.get("path", "")
    content = body.get("content", "")
    if not path:
        raise HTTPException(400, "path required")
    result = await write_file(member_id, path, content)
    if result.startswith("[错误]"):
        raise HTTPException(400, result)
    return {"ok": True}


@router.post("/api/members/{member_id}/workspace/init")
async def init_workspace(member_id: int):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    await init_bot_workspace(bot)
    return {"ok": True, "files": list_workspace_tree(member_id)}


# ---------------------------------------------------------------------------
# Skill status API
# ---------------------------------------------------------------------------

@router.get("/api/members/{member_id}/skills")
async def get_skills(member_id: int, group_id: int | None = None):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    skills = list_skills_all(member_id, group_id=group_id, role=bot.get("role"))
    return {"skills": skills}


@router.put("/api/members/{member_id}/skills/{skill_name}/status")
async def set_skill_status(member_id: int, skill_name: str, body: dict):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    new_status = body.get("status")
    if new_status not in ("active", "disabled"):
        raise HTTPException(400, "status must be 'active' or 'disabled'")
    result = update_skill_status(member_id, skill_name, new_status)
    return {"ok": True, "message": result}


@router.post("/api/members/{member_id}/skills/learned/{skill_name}/approve")
async def approve_skill(member_id: int, skill_name: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    result = approve_draft_skill(member_id, skill_name)
    return {"ok": True, "message": result}


@router.post("/api/members/{member_id}/skills/learned/{skill_name}/reject")
async def reject_skill(member_id: int, skill_name: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    result = reject_draft_skill(member_id, skill_name)
    return {"ok": True, "message": result}
