"""Scope-descriptor skill API: browse/read/write/copy skills at any layer, plus
role-catalog listing. Path-safety lives entirely in skills.scope.parse_descriptor
(_safe_segment); this module never builds a path by hand. Auth is router-level
(token-only, DFT-082)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from skills.role_catalog import list_role_catalog
from skills.scope import parse_descriptor
from skills.store import SkillStore
from workspace import layout

router = APIRouter()
_store = SkillStore()


def _scope(descriptor: str):
    try:
        return parse_descriptor(descriptor)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/skills")
async def list_scope_skills(scope: str):
    return {"skills": _store.list(_scope(scope))}


@router.get("/api/skills/content")
async def read_scope_skill(scope: str, name: str):
    try:
        content = _store.read(_scope(scope), name)
    except ValueError as e:           # unsafe name
        raise HTTPException(400, str(e))
    except (FileNotFoundError, NotADirectoryError):
        raise HTTPException(404, f"skill not found: {name!r}")
    return {"name": name, "content": content}


class WriteSkillRequest(BaseModel):
    scope: str
    name: str
    content: str


class CopySkillRequest(BaseModel):
    src: str
    name: str
    dst: str


@router.post("/api/skills")
async def write_scope_skill(req: WriteSkillRequest):
    try:
        return _store.write(_scope(req.scope), req.name, req.content)
    except ValueError as e:           # unsafe name
        raise HTTPException(400, str(e))


@router.delete("/api/skills")
async def delete_scope_skill(scope: str, name: str):
    try:
        _store.delete(_scope(scope), name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/skills/copy")
async def copy_scope_skill(req: CopySkillRequest):
    try:
        _store.copy(_scope(req.src), req.name, _scope(req.dst))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (FileNotFoundError, NotADirectoryError):
        raise HTTPException(404, f"source skill not found: {req.name!r}")
    return {"ok": True}


@router.get("/api/templates/roles")
async def list_template_roles(lang: str = "zh"):
    return {"lang": lang, "roles": list_role_catalog(layout.templates_roles_dir(lang))}


@router.get("/api/groups/{group_id}/roles")
async def list_group_roles(group_id: int):
    return {"group_id": group_id, "roles": list_role_catalog(layout.group_roles_dir(group_id))}
