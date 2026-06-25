import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Optional

from . import constants as C
from .metadata import parse_skill_meta
from .composer import merge_layers
from .cache import CachedScan
from .sources.base import ScanCtx
from .sources.system import SystemPoolSource
from .sources.group import GroupSource
from .sources.role import RoleSource
from .sources.learned import LearnedSource

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


def _sources(bot_id: int, group_id: Optional[int], role: Optional[str]):
    """Instantiate the four per-layer SkillSource objects for this scan key.

    Path resolution inside each source reads ``skills.constants`` live, so
    test-time monkeypatching of ``constants.{WORKSPACE_ROOT,SYSTEM_SKILLS_ROOT,
    ROLES_ROOT,bot_ws}`` is honored (single source of truth)."""
    ctx = ScanCtx(bot_id, group_id, role)
    return (
        SystemPoolSource(ctx),
        GroupSource(ctx),
        RoleSource(ctx),
        LearnedSource(ctx),
    )


def _scan_signature(bot_id: int, group_id: Optional[int] = None,
                    role: Optional[str] = None) -> tuple:
    """Cheap fingerprint of all skill files for this (bot, group, role).

    Aggregates each source's own ``signature()`` (mtime_ns + size stats, no
    content reads) so a hit covers exactly the same dirs the full scan would —
    detecting any add / delete / edit at a fraction of the cost."""
    sysm, grp, rol, lrn = _sources(bot_id, group_id, role)
    union: list = []
    for src in (sysm, grp, rol, lrn):
        union.extend(src.signature())
    return tuple(sorted(union))


async def list_skills(bot_id: int, group_id: Optional[int] = None) -> List[Dict]:
    """Asynchronous listing of skills in bot's personal skills/ dir."""
    return await asyncio.to_thread(_list_skills_sync, bot_id, group_id)


def _list_skills_sync(bot_id: int, group_id: Optional[int] = None) -> List[Dict]:
    """Internal synchronous personal skill list (personal layer only)."""
    ws = C.bot_ws(bot_id, group_id)
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
    """Uncached four-layer scan — a thin facade over the SkillSource classes.

    Instantiates SystemPoolSource / GroupSource / RoleSource / LearnedSource
    (each resolving its dir from ``skills.constants`` live), enumerates them, and
    hands the per-layer results to ``composer.merge_layers`` which applies A1
    protection, A3 deep-merge, A5/C1/C2 diagnostics, injected calc and sort.
    """
    sysm, grp, rol, lrn = _sources(bot_id, group_id, role)
    return merge_layers(
        sysm.enumerate(),
        grp.enumerate(),
        rol.enumerate(),
        lrn.enumerate(),
    )
