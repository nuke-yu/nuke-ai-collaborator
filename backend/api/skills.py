"""Scope-descriptor skill API: browse/read/write/copy skills at any layer, plus
role-catalog listing. Path-safety lives entirely in skills.scope.parse_descriptor
(_safe_segment); this module never builds a path by hand. Auth is router-level
(token-only, DFT-082)."""
from fastapi import APIRouter, HTTPException

from skills.scope import parse_descriptor
from skills.store import SkillStore

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
