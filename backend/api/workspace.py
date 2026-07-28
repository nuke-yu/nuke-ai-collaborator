from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
import asyncio
from pathlib import Path
from core import auth
from core.auth import verify_token
from db import ensure_group_db_ready, get_db, get_member, global_db
from memory.bootstrap import build_learning_client
from memory.contracts import ApproveSkillCandidate, ListSkillCandidates
from memory.domain import MemoryScope
from runtime.dbpaths import group_db_path
from workspace import layout
from workspace import (
    list_workspace_tree, read_file, write_file, make_dir, delete_path, bot_workspace, init_bot_workspace,
    list_file_history, read_file_history_version, group_workspace, walk_visible,
)
from skills import (
    list_skills_all, update_skill_status, approve_draft_skill, reject_draft_skill,
    skill_path, strip_frontmatter,
    SYSTEM_SKILLS_ROOT, bot_ws as _skills_bot_ws,
)
from ai.client import call_ai_once, AIError

router = APIRouter()
# Preview is a SEPARATE router with NO router-level auth dependency: a new browser
# tab can't send an Authorization header, so this endpoint self-authenticates via
# the JWT carried in the URL path. main.py includes it without get_current_user.
preview_router = APIRouter()


async def _authorized_memory_skill_scope(
    member_id: int, user: dict
) -> MemoryScope:
    uid = int(user["uid"])
    async with global_db() as db:
        bot = await get_member(db, member_id)
        if not bot or bot["type"] != "bot":
            raise HTTPException(404, "Bot not found")
        async with db.execute(
            """SELECT 1 FROM group_memberships
               WHERE user_id=? AND group_id=?""",
            (uid, bot["group_id"]),
        ) as cur:
            membership = await cur.fetchone()
    if membership is None:
        raise HTTPException(404, "Bot not found")
    await ensure_group_db_ready(group_db_path(int(bot["group_id"])))
    return MemoryScope.bot(
        group_id=int(bot["group_id"]),
        bot_id=member_id,
        actor_id=f"user:{uid}",
        user_id=uid,
        purpose="canonical_skill_review",
    )


@router.get("/api/members/{member_id}/workspace")
async def get_workspace_tree(member_id: int):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    return list_workspace_tree(member_id, bot["group_id"], role=bot.get("role"))


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
    return {"ok": True, "result": result, "files": list_workspace_tree(member_id, bot["group_id"], role=bot.get("role"))}


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
    return {"ok": True, "result": result, "files": list_workspace_tree(member_id, bot["group_id"], role=bot.get("role"))}


@router.post("/api/members/{member_id}/workspace/init")
async def init_workspace(member_id: int):
    async with get_db() as db:
        bot = await get_member(db, member_id)
    if not bot or bot["type"] != "bot":
        raise HTTPException(404, "Bot not found")
    await init_bot_workspace(bot)
    return {"ok": True, "files": list_workspace_tree(member_id, bot["group_id"], role=bot.get("role"))}


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


@router.get("/api/members/{member_id}/memory-skills/candidates")
async def get_memory_skill_candidates(
    member_id: int,
    user=Depends(auth.get_current_user),
):
    scope = await _authorized_memory_skill_scope(member_id, user)
    candidates = await build_learning_client().list_skill_candidates(
        ListSkillCandidates(scope=scope)
    )
    return {"candidates": [asdict(candidate) for candidate in candidates]}


@router.post(
    "/api/members/{member_id}/memory-skills/{skill_id}/approve"
)
async def approve_memory_skill_candidate(
    member_id: int,
    skill_id: str,
    body: dict,
    user=Depends(auth.get_current_user),
):
    scope = await _authorized_memory_skill_scope(member_id, user)
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "approval reason is required")
    try:
        approved = await build_learning_client().approve_skill_candidate(
            ApproveSkillCandidate(
                scope=scope,
                skill_id=skill_id,
                reason=reason,
            )
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not approved:
        raise HTTPException(409, "Skill candidate is no longer reviewable")
    return {"ok": True, "skill_id": skill_id, "maturity": "active"}


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
        sdir = layout.group_shared_dir(group_id) / "skills"
    elif layer == "role" and bot.get("role"):
        sdir = layout.group_roles_dir(group_id) / bot["role"] / "skills"
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


def _resolve_preview_path(group_id: int, file_path: str) -> Path | None:
    """Resolve a preview request to a real file inside the group's shared
    workspace, or None if it escapes (path-traversal guard)."""
    root = layout.group_shared_dir(group_id).resolve()
    target = (root / file_path).resolve()
    if not target.is_relative_to(root):
        return None
    return target


@preview_router.get("/api/groups/{group_id}/workspace/preview/{token}/{file_path:path}")
async def preview_workspace_file(group_id: int, token: str, file_path: str):
    """Serve a workspace file rendered in the browser (real Content-Type), so an
    HTML project can be opened/played directly. Auth is the JWT carried in the
    path prefix (a new browser tab can't send an Authorization header, and a
    path-prefix token survives the page's relative asset requests, e.g.
    ./style.css → preview/<token>/.../style.css). Path is sandboxed to the
    group's shared workspace. Per DFT-082 we only check token validity, not a
    strict per-user membership match."""
    if not verify_token(token):
        raise HTTPException(401, "invalid or expired token")
    target = await asyncio.to_thread(_resolve_preview_path, group_id, file_path)
    if target is None:
        raise HTTPException(403, "path escapes workspace")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(str(target))  # media type inferred from extension


@router.get("/api/groups/{group_id}/workspace")
async def get_shared_workspace_tree(group_id: int):
    """Return shared group workspace tree (workspace/<project>/*, docs/, prs/, BOARD.md, etc.)."""
    def _list():
        ws = group_workspace(group_id)
        # 用剪枝遍历替代 rglob("*")：共享区常含代码仓库/node_modules，急切全树枚举会让该接口
        # 极慢并返回巨大 JSON。walk_visible 跳过 node_modules/.git/.history 等并封顶；
        # UI 面向，skip_hidden=False 保留 .gitignore 等 dotfile 给人看。
        paths, _ = walk_visible(ws, max_entries=5000, skip_hidden=False)
        return [
            {"path": str(p.relative_to(ws)).replace("\\", "/"), "name": p.name, "is_dir": p.is_dir()}
            for p in paths
        ]
    return await asyncio.to_thread(_list)
