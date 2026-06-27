"""External skill import pipeline (Plan B §4).

Given a LOCAL repo directory (the git clone is a thin wrapper, clone_and_import),
find every `<name>/SKILL.md`, validate it, copy valid skills into the target
pool with symlink-escape protection (reusing SkillStore.copy), and write an
external_skills registry row per imported skill. Imported skills are untrusted:
inline shell is already inert (DFT-022); we additionally record high-privilege
tool hits for the operator UI.
"""
from pathlib import Path

from . import constants as C
from . import registry
from .metadata import parse_skill_meta, _is_safe_name
from .store import SkillStore
from workspace import layout


class _DirScope:
    """Minimal scope shim for SkillStore.copy: `dir()` returns a fixed dir."""
    def __init__(self, d: Path):
        self._d = d

    def dir(self) -> Path:
        return self._d


def classify_platforms(meta: dict) -> str:
    return meta.get("platforms") or "pure"


def scan_high_privilege(skill_dir: Path) -> str:
    """Comma-joined HIGH_PRIVILEGE_TOOLS mentioned anywhere in the skill's text."""
    texts = []
    for p in skill_dir.rglob("*.md"):
        try:
            texts.append(p.read_text(encoding="utf-8").lower())
        except Exception:
            continue
    blob = "\n".join(texts)
    return ",".join(tool for tool in C.HIGH_PRIVILEGE_TOOLS if tool in blob)


def _safe_dest(name: str) -> None:
    if not _is_safe_name(name):
        raise ValueError(f"unsafe skill name: {name!r}")


def _pool_dir(scope_kind: str, group_id: int) -> Path:
    if scope_kind == "global":
        return layout.external_global_skills_dir()
    return layout.group_external_skills_dir(group_id)


async def import_from_dir(repo_dir, scope_kind: str, group_id: int,
                          source_url: str, ref: str, commit_sha: str,
                          imported_by: int | None) -> dict:
    repo_dir = Path(repo_dir)
    pool = _pool_dir(scope_kind, group_id)
    store = SkillStore()
    dst_scope = _DirScope(pool)

    imported, rejected = [], []
    # Every directory holding a SKILL.md is one skill.
    for skill_md in sorted(repo_dir.rglob("SKILL.md")):
        skill_dir = skill_md.parent
        name = skill_dir.name
        try:
            _safe_dest(name)
        except ValueError as e:
            rejected.append({"path": str(skill_dir), "reason": str(e)})
            continue

        # `name` is the (already safe-validated) directory name; parse_skill_meta
        # surfaces description from frontmatter (it does not echo back `name`).
        meta = parse_skill_meta(skill_md)
        if not meta.get("description"):
            rejected.append({"path": str(skill_dir), "reason": "missing description"})
            continue

        platforms = classify_platforms(meta)
        high_priv = scan_high_privilege(skill_dir)
        version = meta.get("version", "")

        # Copy into the pool (symlink-escape protection lives in SkillStore.copy).
        # src.dir() is the skill's PARENT so src.dir()/name resolves to the folder.
        src_scope = _DirScope(skill_dir.parent)
        try:
            store.copy(src_scope, name, dst_scope)
        except (ValueError, FileNotFoundError) as e:
            rejected.append({"path": str(skill_dir), "reason": f"copy failed: {e}"})
            continue

        try:
            rid = await registry.register(
                name, scope_kind, group_id, source_url, ref, commit_sha,
                version, platforms, high_priv, imported_by,
            )
        except ValueError:
            rejected.append({"path": str(skill_dir), "reason": "duplicate name in scope"})
            continue

        imported.append({"id": rid, "name": name, "version": version,
                         "platforms": platforms, "high_privilege": high_priv})

    return {"imported": imported, "rejected": rejected}
