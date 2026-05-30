import asyncio
from pathlib import Path

from .constants import WORKSPACE_ROOT, SYSTEM_SKILLS_ROOT, ROLES_ROOT, bot_ws
from .metadata import skill_path, parse_skill_meta


def _scan_dir(path: Path, layer: str) -> list[dict]:
    """Scan a skills directory and return skill list with layer tag."""
    if not path.exists():
        return []
    seen: set = set()
    result = []
    for p in sorted(path.iterdir()):
        if p.is_dir():
            sf = p / "SKILL.md"
            if sf.exists() and p.name not in seen:
                seen.add(p.name)
                meta = parse_skill_meta(sf)
                meta["layer"] = meta.get("layer") or layer
                result.append({"name": p.name, "type": "md", **meta})
        elif p.suffix == ".md" and p.stem not in seen:
            seen.add(p.stem)
            meta = parse_skill_meta(p)
            meta["layer"] = meta.get("layer") or layer
            result.append({"name": p.stem, "type": "md", **meta})
        elif p.suffix == ".py" and p.stem not in seen:
            seen.add(p.stem)
            result.append({"name": p.stem, "type": "py", "layer": layer,
                           "description": "(代码技能)", "always": False,
                           "status": "active", "when_to_use": "", "learns": False})
    return result


def list_skills(bot_id: int) -> list[dict]:
    """List skills in bot's personal skills/ dir (no layer merging)."""
    ws = bot_ws(bot_id)
    skills_dir = ws / "skills"
    if not skills_dir.exists():
        return []
    seen: set = set()
    result = []
    for p in sorted(skills_dir.iterdir()):
        if p.is_dir():
            sf = p / "SKILL.md"
            if sf.exists():
                seen.add(p.name)
                meta = parse_skill_meta(sf)
                result.append({"name": p.name, "type": "md", **meta})
        elif p.suffix == ".md" and p.stem not in seen:
            seen.add(p.stem)
            meta = parse_skill_meta(p)
            result.append({"name": p.stem, "type": "md", **meta})
        elif p.suffix == ".py" and p.stem not in seen:
            seen.add(p.stem)
            result.append({"name": p.stem, "type": "py",
                           "description": "(代码技能，M3)", "always": False})
    return result


async def list_skills_all(bot_id: int, group_id: int | None = None,
                         role: str | None = None) -> list[dict]:
    """Asynchronous wrapper for four-layer scan to avoid blocking the event loop."""
    return await asyncio.to_thread(_list_skills_all_sync, bot_id, group_id, role)


def _list_skills_all_sync(bot_id: int, group_id: int | None = None,
                        role: str | None = None) -> list[dict]:
    """Internal synchronous implementation of four-layer scan."""
    merged: dict[str, dict] = {}

    # L1 System
    for s in _scan_dir(SYSTEM_SKILLS_ROOT, "system"):
        merged[s["name"]] = s

    # L2 Group
    if group_id:
        group_path = WORKSPACE_ROOT / f"group_{group_id}" / "shared" / "skills"
        for s in _scan_dir(group_path, "group"):
            merged[s["name"]] = s

    # L3 Role
    if role:
        for s in _scan_dir(ROLES_ROOT / role / "skills", "role"):
            merged[s["name"]] = s

    # L4 Learned/active — directory location is the source of truth for status
    ws = bot_ws(bot_id)
    for s in _scan_dir(ws / "skills" / "learned" / "active", "learned"):
        s["status"] = "active"
        merged[s["name"]] = s

    # Personal (overrides all earlier layers)
    skills_dir = ws / "skills"
    if skills_dir.exists():
        for p in sorted(skills_dir.iterdir()):
            if p.is_dir() and p.name == "learned":
                continue
            if p.is_dir():
                sf = p / "SKILL.md"
                if sf.exists():
                    meta = parse_skill_meta(sf)
                    meta["layer"] = meta.get("layer") or "personal"
                    merged[p.name] = {"name": p.name, "type": "md", **meta}
            elif p.suffix == ".md":
                meta = parse_skill_meta(p)
                meta["layer"] = meta.get("layer") or "personal"
                merged[p.stem] = {"name": p.stem, "type": "md", **meta}
            elif p.suffix == ".py":
                merged[p.stem] = {"name": p.stem, "type": "py", "layer": "personal",
                                   "description": "(代码技能)", "always": False,
                                   "status": "active", "when_to_use": "", "learns": False}

    # L4 Draft — listed separately, never injected
    drafts = []
    draft_dir = ws / "skills" / "learned" / "draft"
    if draft_dir.exists():
        for s in _scan_dir(draft_dir, "learned"):
            s["status"] = "draft"
            drafts.append(s)

    # Compute injected field
    result = []
    for s in merged.values():
        status = s.get("status", "active")
        if status in ("disabled", "deprecated"):
            s["injected"] = None
        elif s.get("always"):
            s["injected"] = "full"
        else:
            s["injected"] = "metadata"
        result.append(s)

    _LAYER_ORDER = {"system": 0, "group": 1, "role": 2, "learned": 3, "personal": 4}
    result.sort(key=lambda x: (_LAYER_ORDER.get(x.get("layer", ""), 5), x["name"]))
    result.extend(drafts)
    return result
