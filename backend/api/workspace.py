from fastapi import APIRouter, HTTPException
import asyncio
from pathlib import Path
from db import get_db, get_member
from workspace import (
    list_workspace_tree, read_file, write_file, make_dir, delete_path, bot_workspace, init_bot_workspace,
    list_file_history, read_file_history_version, group_workspace,
)
from skills import (
    list_skills_all, update_skill_status, approve_draft_skill, reject_draft_skill,
    skill_path, strip_frontmatter,
    WORKSPACE_ROOT, SYSTEM_SKILLS_ROOT, ROLES_ROOT, bot_ws as _skills_bot_ws,
)
from ai.client import call_ai_once, AIError

router = APIRouter()


@router.get("/api/members/{member_id}/workspace")
async def get_workspace_tree(member_id: int):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    return list_workspace_tree(member_id, bot["group_id"])


@router.get("/api/members/{member_id}/workspace/file")
async def get_workspace_file(member_id: int, path: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    content = await read_file(member_id, path, group_id=bot["group_id"])
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
    result = await write_file(member_id, path, content, group_id=bot["group_id"])
    if result.startswith("[错误]"):
        raise HTTPException(400, result)
    return {"ok": True}


@router.post("/api/members/{member_id}/workspace/dir")
async def make_workspace_dir(member_id: int, body: dict):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    path = (body or {}).get("path", "").strip()
    if not path:
        raise HTTPException(400, "path required")
    result = await asyncio.to_thread(make_dir, member_id, path, bot["group_id"])
    if result.startswith("[错误]"):
        raise HTTPException(400, result)
    return {"ok": True, "result": result, "files": list_workspace_tree(member_id, bot["group_id"])}


@router.delete("/api/members/{member_id}/workspace/file")
async def delete_workspace_file(member_id: int, path: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    if not path:
        raise HTTPException(400, "path required")
    result = await asyncio.to_thread(delete_path, member_id, path, bot["group_id"])
    if not result.startswith("已删除"):
        raise HTTPException(400, result)
    return {"ok": True, "result": result, "files": list_workspace_tree(member_id, bot["group_id"])}


@router.post("/api/members/{member_id}/workspace/init")
async def init_workspace(member_id: int):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    await init_bot_workspace(bot)
    return {"ok": True, "files": list_workspace_tree(member_id, bot["group_id"])}


# ---------------------------------------------------------------------------
# Skill status API
# ---------------------------------------------------------------------------

@router.get("/api/members/{member_id}/skills")
async def get_skills(member_id: int, group_id: int | None = None):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    # bot 恰属一个群组：用 bot 自身 group 作真相源（私有技能路径 + 群组共享技能层都靠它），
    # 不信任可缺省的 query 参数，否则私有技能落 group_{gid}/bots/ 却按扁平路径去找会漏。
    skills = await list_skills_all(member_id, group_id=bot["group_id"], role=bot.get("role"))
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
    result = await asyncio.to_thread(update_skill_status, member_id, skill_name, new_status, bot["group_id"])
    return {"ok": True, "message": result}


@router.post("/api/members/{member_id}/skills/learned/{skill_name}/approve")
async def approve_skill(member_id: int, skill_name: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    result = await asyncio.to_thread(approve_draft_skill, member_id, skill_name, bot["group_id"])
    if result.startswith("["):
        raise HTTPException(404, result)
    return {"ok": True, "message": result}


@router.post("/api/members/{member_id}/skills/{skill_name}/test")
async def test_skill(member_id: int, skill_name: str, body: dict):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message required")
    group_id = bot["group_id"]   # bot 自身群组为真相源（私有/群组技能路径都靠它）
    skills = await list_skills_all(member_id, group_id=group_id, role=bot.get("role"))
    skill_info = next((s for s in skills if s["name"] == skill_name), None)
    if not skill_info:
        raise HTTPException(404, f"技能 '{skill_name}' 不存在")
    # Locate the skill file across all layers
    layer = skill_info.get("layer", "personal")
    if layer == "system":
        sdir = SYSTEM_SKILLS_ROOT
    elif layer == "group" and group_id:
        sdir = WORKSPACE_ROOT / f"group_{group_id}" / "shared" / "skills"
    elif layer == "role" and bot.get("role"):
        from skills.constants import ROLES_ROOT as _ROLES
        sdir = _ROLES / bot["role"] / "skills"
    elif layer == "learned":
        sdir = _skills_bot_ws(member_id, bot["group_id"]) / "skills" / "learned" / "active"
    else:
        sdir = _skills_bot_ws(member_id, bot["group_id"]) / "skills"
    path, kind = skill_path(sdir, skill_name)
    if path is None or kind != "md":
        raise HTTPException(404, "技能文件未找到")
    raw = path.read_text(encoding="utf-8")
    system_prompt = strip_frontmatter(raw).strip() or f"你是一个助手。"
    try:
        result = await call_ai_once(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": message}],
            provider=bot.get("model_provider", "deepseek"),
            model=bot.get("model_name", "deepseek-chat"),
            temperature=bot.get("temperature", 0.7),
            max_tokens=min(int(bot.get("max_tokens") or 4096), 1024),
        )
    except AIError as e:
        raise HTTPException(502, str(e))
    response = result.get("content", "") if result["type"] == "text" else "[工具调用响应，不适用于测试]"
    return {"response": response}


@router.get("/api/members/{member_id}/workspace/history")
async def get_file_history(member_id: int, path: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    return {"path": path, "versions": list_file_history(member_id, path, bot["group_id"])}


@router.get("/api/members/{member_id}/workspace/history/version")
async def get_history_version(member_id: int, path: str, ts: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    content = read_file_history_version(member_id, path, ts, bot["group_id"])
    if content.startswith("[错误]") or content.startswith("[版本不存在]"):
        raise HTTPException(404, content)
    return {"path": path, "ts": ts, "content": content}


@router.post("/api/members/{member_id}/skills/learned/{skill_name}/reject")
async def reject_skill(member_id: int, skill_name: str):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    result = await asyncio.to_thread(reject_draft_skill, member_id, skill_name, bot["group_id"])
    if result.startswith("["):
        raise HTTPException(404, result)
    return {"ok": True, "message": result}


@router.get("/api/groups/{group_id}/workspace")
async def get_shared_workspace_tree(group_id: int):
    """Return shared group workspace tree (workspace/<project>/*, docs/, prs/, BOARD.md, etc.)."""
    def _list():
        ws = group_workspace(group_id)
        result = []
        for p in ws.rglob("*"):
            if p.is_relative_to(ws) and not str(p).startswith(str(ws / ".history")):
                rel = str(p.relative_to(ws)).replace("\\", "/")
                result.append({"path": rel, "name": p.name, "is_dir": p.is_dir()})
        return result
    return await asyncio.to_thread(_list)

