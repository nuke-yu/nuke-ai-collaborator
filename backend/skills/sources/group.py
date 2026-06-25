# backend/skills/sources/group.py
from typing import List
from workspace import layout
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


class GroupSource:
    layer = "group"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    def _dir(self):
        if not self.ctx.group_id:
            return None
        return layout.group_shared_dir(self.ctx.group_id) / "skills"

    def enumerate(self) -> List[SkillEntry]:
        d = self._dir()
        return scan_dir(d, "group") if d else []

    def signature(self) -> tuple:
        d = self._dir()
        return tuple(dir_signature(d)) if d else ()
