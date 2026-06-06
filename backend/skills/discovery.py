import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Optional

from .constants import WORKSPACE_ROOT, SYSTEM_SKILLS_ROOT, ROLES_ROOT, bot_ws
from .metadata import skill_path, parse_skill_meta

log = logging.getLogger(__name__)


def _scan_dir_sync(path: Path, layer: str) -> List[Dict]:
    """Internal synchronous directory scanner."""
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
                result.append({"name": p.name, "type": "md", "path": sf, **meta})
        elif p.suffix == ".md" and p.stem not in seen:
            seen.add(p.stem)
            meta = parse_skill_meta(p)
            meta["layer"] = meta.get("layer") or layer
            result.append({"name": p.stem, "type": "md", "path": p, **meta})
        elif p.suffix == ".py" and p.stem not in seen:
            seen.add(p.stem)
            result.append({
                "name": p.stem, "type": "py", "layer": layer,
                "description": "(代码技能)", "always": False,
                "status": "active", "when_to_use": "", "learns": False,
                "is_stub": False, "fm_keys": [], "path": p
            })
    return result


def _scan_personal_layer_sync(skills_dir: Path) -> Dict[str, Dict]:
    personal = {}
    if not skills_dir.exists():
        return personal
    for p in sorted(skills_dir.iterdir()):
        if p.is_dir() and p.name == "learned":
            continue
        if p.is_dir():
            sf = p / "SKILL.md"
            if sf.exists():
                meta = parse_skill_meta(sf)
                meta["layer"] = meta.get("layer") or "personal"
                personal[p.name] = {"name": p.name, "type": "md", "path": sf, **meta}
        elif p.suffix == ".md":
            meta = parse_skill_meta(p)
            meta["layer"] = meta.get("layer") or "personal"
            personal[p.stem] = {"name": p.stem, "type": "md", "path": p, **meta}
        elif p.suffix == ".py":
            personal[p.stem] = {
                "name": p.stem, "type": "py", "layer": "personal",
                "description": "(代码技能)", "always": False,
                "status": "active", "when_to_use": "", "learns": False,
                "is_stub": False, "fm_keys": [], "path": p
            }
    return personal


async def list_skills(bot_id: int) -> List[Dict]:
    """Asynchronous listing of skills in bot's personal skills/ dir."""
    return await asyncio.to_thread(_list_skills_sync, bot_id)


def _list_skills_sync(bot_id: int) -> List[Dict]:
    """Internal synchronous personal skill list."""
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
                result.append({"name": p.name, "type": "md", "path": sf, **meta})
        elif p.suffix == ".md" and p.stem not in seen:
            seen.add(p.stem)
            meta = parse_skill_meta(p)
            result.append({"name": p.stem, "type": "md", "path": p, **meta})
        elif p.suffix == ".py" and p.stem not in seen:
            seen.add(p.stem)
            result.append({
                "name": p.stem, "type": "py", "path": p,
                "description": "(代码技能，M3)", "always": False,
                "is_stub": False, "fm_keys": []
            })
    return result


async def list_skills_all(bot_id: int, group_id: Optional[int] = None,
                         role: Optional[str] = None) -> List[Dict]:
    """Asynchronous wrapper for four-layer scan to avoid blocking the event loop."""
    return await asyncio.to_thread(_list_skills_all_sync, bot_id, group_id, role)


def _merge_skill_entry(merged: Dict[str, Dict], incoming: Dict) -> None:
    """Helper to merge skills with system protection (A1) and stub fallback (A3)."""
    name = incoming["name"]
    existing = merged.get(name)
    if not existing:
        merged[name] = incoming
        return

    # A1: System protection (First-Wins for L1 System layer)
    if existing.get("layer") == "system":
        log.warning(
            "Collision Warning: System skill '%s' is protected and cannot be shadowed by lower layer skill at '%s'. "
            "Winner: '%s', Loser: '%s'",
            name, incoming.get("path"), existing.get("path"), incoming.get("path")
        )
        return

    # A3: If overriding, perform a merged update (Deep Merge)
    merged_entry = dict(existing)
    
    # Update with keys explicitly defined in the frontmatter (fm_keys) of the incoming file
    fm_keys = incoming.get("fm_keys", [])
    for key in fm_keys:
        if key in incoming:
            merged_entry[key] = incoming[key]
            
    # Copy special status/layer override flags
    merged_entry["layer"] = incoming.get("layer", merged_entry.get("layer"))
    merged_entry["status"] = incoming.get("status", merged_entry.get("status"))
    merged_entry["is_stub"] = incoming.get("is_stub", False)
    
    # If the incoming one is NOT a stub, we update the content path, type and all metadata
    if not incoming.get("is_stub", False):
        merged_entry["path"] = incoming.get("path")
        merged_entry["type"] = incoming.get("type", "md")
        for key, value in incoming.items():
            if key not in ["fm_keys", "path", "type"]:
                merged_entry[key] = value

    merged[name] = merged_entry


def _list_skills_all_sync(bot_id: int, group_id: Optional[int] = None,
                         role: Optional[str] = None) -> List[Dict]:
    """Internal synchronous implementation of four-layer scan."""
    merged: Dict[str, Dict] = {}

    # L1 System
    for s in _scan_dir_sync(SYSTEM_SKILLS_ROOT, "system"):
        _merge_skill_entry(merged, s)

    # L2 Group
    if group_id:
        group_path = WORKSPACE_ROOT / f"group_{group_id}" / "shared" / "skills"
        for s in _scan_dir_sync(group_path, "group"):
            _merge_skill_entry(merged, s)

    # L3 Role
    if role:
        for s in _scan_dir_sync(ROLES_ROOT / role / "skills", "role"):
            _merge_skill_entry(merged, s)

    # L4 Learned/active
    ws = bot_ws(bot_id)
    for s in _scan_dir_sync(ws / "skills" / "learned" / "active", "learned"):
        s["status"] = "active"
        _merge_skill_entry(merged, s)

    # Personal (overrides all earlier layers)
    personal_skills = _scan_personal_layer_sync(ws / "skills")
    for name, s in personal_skills.items():
        _merge_skill_entry(merged, s)

    # L4 Draft
    drafts = []
    draft_dir = ws / "skills" / "learned" / "draft"
    if draft_dir.exists():
        for s in _scan_dir_sync(draft_dir, "learned"):
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
