import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import List, Dict, Optional

from .constants import WORKSPACE_ROOT, SYSTEM_SKILLS_ROOT, ROLES_ROOT, bot_ws
from .metadata import skill_path, parse_skill_meta

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Four-layer scan cache (avoid re-reading + re-parsing every SKILL.md per turn)
# --------------------------------------------------------------------------- #
# list_skills_all() runs on EVERY prompt build (every bot, every turn). The cache
# is keyed by (bot_id, group_id, role) and validated by a cheap mtime+size
# signature of the relevant skill files, so an edit/add/delete is picked up
# without reading file *contents* on a hit.
#
# Why signature-based rather than watcher-driven invalidation: the SkillWatcher
# and this cache must be in the SAME process to communicate, and module-level
# state does NOT cross fork. The mtime signature is correct in any process,
# independent of whether a watcher runs there. invalidate_skills_cache() is an
# extra same-process fast-path (called by the watcher) — not the correctness
# mechanism.
_SKILLS_CACHE: Dict[tuple, tuple] = {}   # key -> (signature, list[dict])
_CACHE_LOCK = threading.Lock()


def invalidate_skills_cache() -> None:
    """Clear the four-layer scan cache (called by the watcher on skill changes)."""
    with _CACHE_LOCK:
        _SKILLS_CACHE.clear()


def _scan_signature(bot_id: int, group_id: Optional[int], role: Optional[str]) -> tuple:
    """Cheap fingerprint of all skill files for this (bot, group, role).

    Walks the same layer dirs the full scan would, but only stats (mtime_ns,
    size) instead of reading + YAML-parsing each file — detecting any change
    (add / delete / edit) at a fraction of the cost."""
    dirs = [SYSTEM_SKILLS_ROOT]
    if group_id:
        dirs.append(WORKSPACE_ROOT / f"group_{group_id}" / "shared" / "skills")
    if role:
        dirs.append(ROLES_ROOT / role / "skills")
    dirs.append(bot_ws(bot_id, group_id) / "skills")  # personal: root + manual + learned/*

    sig: list = []
    for d in dirs:
        if not d.exists():
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                if fn.endswith((".md", ".py")):
                    fp = os.path.join(root, fn)
                    try:
                        st = os.stat(fp)
                        sig.append((fp, st.st_mtime_ns, st.st_size))
                    except OSError:
                        continue
    return tuple(sorted(sig))


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

    def scan_dir(dir_to_scan: Path):
        if not dir_to_scan.exists():
            return
        for p in sorted(dir_to_scan.iterdir()):
            if p.is_dir():
                if p.name in ("learned", "manual"):
                    continue
                sf = p / "SKILL.md"
                if sf.exists() and p.name not in personal:
                    meta = parse_skill_meta(sf)
                    meta["layer"] = meta.get("layer") or "personal"
                    personal[p.name] = {"name": p.name, "type": "md", "path": sf, **meta}
            elif p.suffix == ".md" and p.stem not in personal:
                meta = parse_skill_meta(p)
                meta["layer"] = meta.get("layer") or "personal"
                personal[p.stem] = {"name": p.stem, "type": "md", "path": p, **meta}
            elif p.suffix == ".py" and p.stem not in personal:
                personal[p.stem] = {
                    "name": p.stem, "type": "py", "layer": "personal",
                    "description": "(代码技能)", "always": False,
                    "status": "active", "when_to_use": "", "learns": False,
                    "is_stub": False, "fm_keys": [], "path": p
                }

    # 1. Scan manual subfolder first if it exists
    scan_dir(skills_dir / "manual")

    # 2. Scan root of skills_dir for backwards compatibility, ignoring learned and manual
    scan_dir(skills_dir)

    return personal


async def list_skills(bot_id: int, group_id: Optional[int] = None) -> List[Dict]:
    """Asynchronous listing of skills in bot's personal skills/ dir."""
    return await asyncio.to_thread(_list_skills_sync, bot_id, group_id)


def _list_skills_sync(bot_id: int, group_id: Optional[int] = None) -> List[Dict]:
    """Internal synchronous personal skill list."""
    ws = bot_ws(bot_id, group_id)
    skills_dir = ws / "skills"
    if not skills_dir.exists():
        return []
    seen: set = set()
    result = []

    def scan_dir(dir_to_scan: Path):
        if not dir_to_scan.exists():
            return
        for p in sorted(dir_to_scan.iterdir()):
            if p.is_dir():
                if p.name in ("learned", "manual"):
                    continue
                sf = p / "SKILL.md"
                if sf.exists() and p.name not in seen:
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

    # 1. Scan manual subfolder first if it exists
    scan_dir(skills_dir / "manual")

    # 2. Scan root of skills_dir for backwards compatibility, ignoring learned and manual
    scan_dir(skills_dir)

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
    is_system = False
    existing_path = existing.get("path")
    if existing_path:
        try:
            is_system = Path(existing_path).resolve().is_relative_to(SYSTEM_SKILLS_ROOT.resolve())
        except (ValueError, OSError):
            pass
    if is_system or existing.get("layer") == "system":
        log.warning(
            "Collision Warning: System skill '%s' is protected and cannot be shadowed by lower layer skill at '%s'. "
            "Winner: '%s', Loser: '%s'",
            name, incoming.get("path"), existing.get("path"), incoming.get("path")
        )
        return

    # A5: Cross-layer override is by design (later layer wins), but a non-stub
    # skill shadowing another layer's real content is worth a diagnostic so the
    # collision is observable (mirrors gsd-2's winner/loser report).
    if not incoming.get("is_stub", False) and not existing.get("is_stub", False):
        if existing.get("path") and incoming.get("path") != existing.get("path"):
            log.info(
                "Skill override: '%s' — winner=%s (%s layer) shadows loser=%s (%s layer)",
                name, incoming.get("path"), incoming.get("layer", "?"),
                existing.get("path"), existing.get("layer", "?"),
            )

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
    """Cached four-layer scan. Returns fresh shallow copies so callers can mutate
    top-level fields (e.g. `injected`) without poisoning the cache."""
    key = (bot_id, group_id, role)
    sig = _scan_signature(bot_id, group_id, role)
    with _CACHE_LOCK:
        entry = _SKILLS_CACHE.get(key)
        if entry is not None and entry[0] == sig:
            return [dict(s) for s in entry[1]]

    result = _compute_skills_all(bot_id, group_id, role)

    with _CACHE_LOCK:
        _SKILLS_CACHE[key] = (sig, [dict(s) for s in result])
    return result


def _compute_skills_all(bot_id: int, group_id: Optional[int] = None,
                        role: Optional[str] = None) -> List[Dict]:
    """Internal synchronous implementation of four-layer scan (uncached)."""
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
    ws = bot_ws(bot_id, group_id)
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
            diagnostics = []
            
            # C1: Check naming collision with active skills
            name = s.get("name")
            if name in merged:
                winner = merged[name]
                winner_layer = winner.get("layer", "unknown")
                diagnostics.append({
                    "type": "collision",
                    "severity": "warning",
                    "message": f"命名冲突：已存在同名的激活技能 '{name}' ({winner_layer} 层)，此草稿将无法直接生效。"
                })
                log.warning("Draft Collision Warning: Draft skill '%s' collides with active skill in '%s' layer.", name, winner_layer)

            # C2: Check high-privilege tools in draft (allowed_tools + body text check)
            allowed_tools = s.get("allowed_tools", [])
            high_privilege_tools = ["run_shell", "write_file"]
            triggered = [t for t in allowed_tools if t in high_privilege_tools]
            
            # Scan file body content for privilege tool mentions
            if s.get("path"):
                try:
                    body_text = Path(s["path"]).read_text(encoding="utf-8").lower()
                    for t in high_privilege_tools:
                        if t in body_text and t not in triggered:
                            triggered.append(t)
                except Exception:
                    pass

            if triggered:
                diagnostics.append({
                    "type": "privilege",
                    "severity": "critical",
                    "message": f"高权安全警告：此草稿技能声明或提及了敏感工具权限（{', '.join(triggered)}），请谨慎审批。"
                })

            s["diagnostics"] = diagnostics
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
