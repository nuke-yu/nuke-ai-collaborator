# backend/skills/sources/system.py
from typing import List
from ..constants import SYSTEM_SKILLS_ROOT
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


class SystemPoolSource:
    layer = "system"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    def enumerate(self) -> List[SkillEntry]:
        return scan_dir(SYSTEM_SKILLS_ROOT, "system")

    def signature(self) -> tuple:
        return tuple(dir_signature(SYSTEM_SKILLS_ROOT))
