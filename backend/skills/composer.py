"""Cross-layer merge engine for the SkillSource architecture.

Receives already-enumerated per-layer results and applies:
  A1  system protection (system skills can't be shadowed)
  A3  deep-merge override
  A5  override diagnostics
  injected calculation (full / metadata / None)
  layer-order sort
  draft append with C1 / C2 diagnostics

The core helpers (_merge_skill_entry, _draft_diagnostics) now live here as the
single source of truth (Task 9 removed discovery.py's own copies). discovery.py
is a thin facade that instantiates the SkillSource classes and calls
merge_layers().
"""
import logging
from pathlib import Path
from typing import Dict, List

from . import constants as C
from .sources.base import SkillEntry

log = logging.getLogger(__name__)

_LAYER_ORDER = {"system": 0, "group": 1, "role": 2,
                "external_global": 2.3, "external_group": 2.6,
                "learned": 3, "personal": 4}


def _merge_skill_entry(merged: Dict[str, SkillEntry], incoming: SkillEntry) -> None:
    """Helper to merge skills with system protection (A1) and stub fallback (A3).

    Verbatim copy from discovery.py:187-244.
    """
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
            is_system = Path(existing_path).resolve().is_relative_to(C.SYSTEM_SKILLS_ROOT.resolve())
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


def _draft_diagnostics(s: SkillEntry, merged: Dict[str, SkillEntry]) -> list:
    """Compute C1 (collision) and C2 (high-privilege) diagnostics for a draft skill.

    Verbatim copy of the diagnostic block from discovery.py:302-338 (inside the
    old draft loop), refactored to a standalone function that returns the list.
    """
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
    high_privilege_tools = list(C.HIGH_PRIVILEGE_TOOLS)
    triggered = [t for t in allowed_tools if t in high_privilege_tools]

    # Scan file body content for privilege tool mentions
    if s.get("path"):
        try:
            body_text = Path(s["path"]).read_text(encoding="utf-8").lower()
            for t in high_privilege_tools:
                if t in body_text and t not in triggered:
                    triggered.append(t)
        except Exception:
            log.warning("skills.composer: failed to read draft body for diagnostics from %s", s["path"], exc_info=True)

    if triggered:
        diagnostics.append({
            "type": "privilege",
            "severity": "critical",
            "message": f"高权安全警告：此草稿技能声明或提及了敏感工具权限（{', '.join(triggered)}），请谨慎审批。"
        })

    return diagnostics


def merge_layers(system: list, group: list, role: list, learned: dict,
                 *, external: list | None = None) -> List[SkillEntry]:
    """Merge enumerated per-layer skill lists into a single ordered result list.

    Order of precedence (later wins, except system which is protected):
      system → group → role → external → learned["active"] → learned["personal"].values()

    After merging, computes the ``injected`` field for each skill, sorts by
    layer order then name, and appends draft skills (learned["draft"]) with
    their C1/C2 diagnostics.

    The output structure is identical to the current discovery._compute_skills_all.
    """
    merged: Dict[str, SkillEntry] = {}
    for s in system:
        _merge_skill_entry(merged, s)
    for s in group:
        _merge_skill_entry(merged, s)
    for s in role:
        _merge_skill_entry(merged, s)
    for s in (external or []):
        _merge_skill_entry(merged, s)
    for s in learned.get("active", []):
        _merge_skill_entry(merged, s)
    for name, s in learned.get("personal", {}).items():
        _merge_skill_entry(merged, s)

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

    result.sort(key=lambda x: (_LAYER_ORDER.get(x.get("layer", ""), 5), x["name"]))

    for s in learned.get("draft", []):
        s["status"] = "draft"
        s["diagnostics"] = _draft_diagnostics(s, merged)
        result.append(s)
    return result
