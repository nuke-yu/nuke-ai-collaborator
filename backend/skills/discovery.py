import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import List, Dict, Optional

from .constants import WORKSPACE_ROOT, SYSTEM_SKILLS_ROOT, ROLES_ROOT, bot_ws
from .metadata import skill_path, parse_skill_meta
from .composer import merge_layers
from .cache import CachedScan

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
_SCAN_CACHE = CachedScan()


def invalidate_skills_cache() -> None:
    """Clear the four-layer scan cache (called by the watcher on skill changes)."""
    _SCAN_CACHE.clear()


def _scan_signature(bot_id: int, group_id: Optional[int] = None,
                    role: Optional[str] = None) -> tuple:
    """Cheap fingerprint of all skill files for this (bot, group, role).

    Walks the same layer dirs the full scan would, but only stats (mtime_ns,
    size) instead of reading + YAML-parsing each file — detecting any change
    (add / delete / edit) at a fraction of the cost.

    Uses the module-level constants (which tests may monkey-patch) to ensure
    the signature covers the same directories as _compute_skills_all."""
    import sys
    _self = sys.modules[__name__]
    _SYSTEM_SKILLS_ROOT = getattr(_self, "SYSTEM_SKILLS_ROOT", SYSTEM_SKILLS_ROOT)
    _WORKSPACE_ROOT = getattr(_self, "WORKSPACE_ROOT", WORKSPACE_ROOT)
    _ROLES_ROOT = getattr(_self, "ROLES_ROOT", ROLES_ROOT)
    _bot_ws = getattr(_self, "bot_ws", bot_ws)

    dirs = [_SYSTEM_SKILLS_ROOT]
    if group_id:
        dirs.append(_WORKSPACE_ROOT / f"group_{group_id}" / "shared" / "skills")
    if role:
        dirs.append(_ROLES_ROOT / role / "skills")
    dirs.append(_bot_ws(bot_id, group_id) / "skills")  # personal: root + manual + learned/*

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


def _merge_skill_entry(merged: Dict[str, Dict], incoming: Dict) -> None:
    """Helper to merge skills with system protection (A1) and stub fallback (A3).

    Re-exports the same logic as composer._merge_skill_entry, but logs through
    this module's logger (``skills.discovery``) for backward compatibility with
    tests that assert on the logger name."""
    import sys
    _self = sys.modules[__name__]
    _SYSTEM_SKILLS_ROOT = getattr(_self, "SYSTEM_SKILLS_ROOT", SYSTEM_SKILLS_ROOT)

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
            is_system = Path(existing_path).resolve().is_relative_to(_SYSTEM_SKILLS_ROOT.resolve())
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


async def list_skills(bot_id: int, group_id: Optional[int] = None) -> List[Dict]:
    """Asynchronous listing of skills in bot's personal skills/ dir."""
    return await asyncio.to_thread(_list_skills_sync, bot_id, group_id)


def _list_skills_sync(bot_id: int, group_id: Optional[int] = None) -> List[Dict]:
    """Internal synchronous personal skill list."""
    import sys
    _self = sys.modules[__name__]
    _bot_ws = getattr(_self, "bot_ws", bot_ws)

    ws = _bot_ws(bot_id, group_id)
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


def _list_skills_all_sync(bot_id: int, group_id: Optional[int] = None,
                          role: Optional[str] = None) -> List[Dict]:
    """Cached four-layer scan. Returns fresh shallow copies so callers can mutate
    top-level fields (e.g. `injected`) without poisoning the cache."""
    key = (bot_id, group_id, role)
    sig = _scan_signature(bot_id, group_id, role)
    return _SCAN_CACHE.get(key, sig, lambda: _compute_skills_all(bot_id, group_id, role))


def _compute_skills_all(bot_id: int, group_id: Optional[int] = None,
                        role: Optional[str] = None) -> List[Dict]:
    """Internal synchronous implementation of four-layer scan (uncached).

    Reads from this module's current attribute bindings (SYSTEM_SKILLS_ROOT,
    WORKSPACE_ROOT, ROLES_ROOT, bot_ws) so that test-time monkey-patching of
    those attributes on ``skills.discovery`` is respected.  Delegates merge
    logic to ``composer.merge_layers`` and uses ``_merge_skill_entry`` defined
    here (which logs through the ``skills.discovery`` logger).
    """
    import sys
    _self = sys.modules[__name__]
    _SYSTEM_SKILLS_ROOT = getattr(_self, "SYSTEM_SKILLS_ROOT", SYSTEM_SKILLS_ROOT)
    _WORKSPACE_ROOT = getattr(_self, "WORKSPACE_ROOT", WORKSPACE_ROOT)
    _ROLES_ROOT = getattr(_self, "ROLES_ROOT", ROLES_ROOT)
    _bot_ws = getattr(_self, "bot_ws", bot_ws)

    # L1 System
    system_skills = _scan_dir_sync(_SYSTEM_SKILLS_ROOT, "system")

    # L2 Group
    group_skills = []
    if group_id:
        group_path = _WORKSPACE_ROOT / f"group_{group_id}" / "shared" / "skills"
        group_skills = _scan_dir_sync(group_path, "group")

    # L3 Role
    role_skills = []
    if role:
        role_skills = _scan_dir_sync(_ROLES_ROOT / role / "skills", "role")

    # L4 Learned + Personal + Draft
    ws = _bot_ws(bot_id, group_id)
    skills_dir = ws / "skills"

    active = _scan_dir_sync(skills_dir / "learned" / "active", "learned")
    for s in active:
        s["status"] = "active"

    personal = _scan_personal_layer_sync(skills_dir)

    draft_raw = []
    draft_dir = skills_dir / "learned" / "draft"
    if draft_dir.exists():
        draft_raw = _scan_dir_sync(draft_dir, "learned")
        for s in draft_raw:
            s["status"] = "draft"

    learned = {
        "active": active,
        "personal": personal,
        "draft": draft_raw,
    }

    return merge_layers(system_skills, group_skills, role_skills, learned)
