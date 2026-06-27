"""Scope-descriptor skill API: browse/read/write/copy skills at any layer, plus
role-catalog listing. Path-safety lives entirely in skills.scope.parse_descriptor
(_safe_segment); this module never builds a path by hand. Auth is router-level
(token-only, DFT-082)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import shutil

from skills.role_catalog import list_role_catalog
from skills.scope import parse_descriptor, _safe_segment
from skills.store import SkillStore
from skills import importer, registry
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


class ImportSkillRequest(BaseModel):
    git_url: str
    ref: str = ""
    scope: object   # "global" or {"group_id": int}


def _scope_kind_group(scope) -> tuple[str, int]:
    if scope == "global":
        return "global", registry.GLOBAL_GROUP_ID
    if isinstance(scope, dict) and "group_id" in scope:
        return "group", int(scope["group_id"])
    raise HTTPException(400, "scope must be 'global' or {group_id}")


@router.post("/api/skills/import")
async def import_external_skill(req: ImportSkillRequest):
    scope_kind, group_id = _scope_kind_group(req.scope)
    try:
        return await importer.clone_and_import(
            req.git_url, req.ref, scope_kind, group_id, imported_by=None
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"import failed: {e}")


@router.get("/api/skills/external")
async def list_external_skills(scope_kind: str | None = None, group_id: int | None = None):
    return {"external": await registry.list_external(scope_kind, group_id)}


@router.delete("/api/skills/external/{external_id}")
async def remove_external_skill(external_id: int):
    row = await registry.remove_external(external_id)
    if row is None:
        raise HTTPException(404, f"external skill not found: {external_id}")
    # Delete the pool files too (registry + disk stay consistent).
    if row["scope_kind"] == "global":
        pool = layout.external_global_skills_dir()
    else:
        pool = layout.group_external_skills_dir(row["group_id"])
    target = pool / row["name"]
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    return {"ok": True}


@router.get("/api/templates/roles")
async def list_template_roles(lang: str = "zh"):
    # `lang` is request input that becomes a path segment — validate it with the
    # same boundary the scope descriptors use (blocks ../ and separators).
    try:
        _safe_segment(lang)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"lang": lang, "roles": list_role_catalog(layout.templates_roles_dir(lang), lang)}


@router.get("/api/groups/{group_id}/roles")
async def list_group_roles(group_id: int, lang: str = "zh"):
    # `lang` selects the display-name language; role identity (dir name) is
    # language-neutral. Validate it as a path-safe segment for consistency.
    try:
        _safe_segment(lang)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"group_id": group_id, "lang": lang,
            "roles": list_role_catalog(layout.group_roles_dir(group_id), lang)}
