# backend/skills/sources/external.py
from typing import List
from workspace import layout
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


class ExternalPoolSource:
    """The two external skill pools as one source.

    Global pool (operator-curated, cross-group) tags entries `external_global`;
    the per-group pool (group-private) tags `external_group`. Enumeration order
    is global-then-group so the group layer wins a name clash during merge.
    Visibility per bot is applied LATER (filter_visible), never here.
    """
    layer = "external"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    def _global_dir(self):
        return layout.external_global_skills_dir()

    def _group_dir(self):
        if not self.ctx.group_id:
            return None
        return layout.group_external_skills_dir(self.ctx.group_id)

    def enumerate(self) -> List[SkillEntry]:
        out = scan_dir(self._global_dir(), "external_global")
        gd = self._group_dir()
        if gd:
            out = out + scan_dir(gd, "external_group")
        return out

    def signature(self) -> tuple:
        sig = list(dir_signature(self._global_dir()))
        gd = self._group_dir()
        if gd:
            sig.extend(dir_signature(gd))
        return tuple(sig)
