# backend/skills/sources/learned.py
from typing import Dict
from pathlib import Path
from .. import constants as C
from ..metadata import parse_skill_meta
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


def _scan_personal(skills_dir: Path) -> Dict[str, SkillEntry]:
    """Verbatim port of discovery._scan_personal_layer_sync."""
    personal: Dict[str, SkillEntry] = {}
    if not skills_dir.exists():
        return personal

    def scan(dir_to_scan: Path):
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

    scan(skills_dir / "manual")
    scan(skills_dir)
    return personal


class LearnedSource:
    layer = "learned"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    @property
    def _base(self) -> Path:
        # Resolved live on every access so a late monkeypatch of C.bot_ws (or
        # of constants.WORKSPACE_ROOT, read through layout) is honored. Matches
        # the original discovery behavior: bot_ws(bot_id, group_id) / "skills".
        return C.bot_ws(self.ctx.bot_id, self.ctx.group_id) / "skills"

    def enumerate(self) -> dict:
        base = self._base
        active = scan_dir(base / "learned" / "active", "learned")
        for s in active:
            s["status"] = "active"
        personal = _scan_personal(base)
        draft = scan_dir(base / "learned" / "draft", "learned")
        for s in draft:
            s["status"] = "draft"
        return {"active": active, "personal": personal, "draft": draft}

    def signature(self) -> tuple:
        return tuple(dir_signature(self._base))
