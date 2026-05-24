from fastapi import APIRouter, HTTPException
from database import get_db, get_member
from workspace import list_workspace_tree, read_file, write_file, _safe_path, bot_workspace, init_bot_workspace

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
