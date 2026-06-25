from typing import List
from .. import constants as C
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


class RoleSource:
    layer = "role"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    def _dir(self):
        if not self.ctx.role:
            return None
        # NOTE: still the global ROLES_ROOT here — flipped to group-internal in Task 12.
        return C.ROLES_ROOT / self.ctx.role / "skills"

    def enumerate(self) -> List[SkillEntry]:
        d = self._dir()
        return scan_dir(d, "role") if d else []

    def signature(self) -> tuple:
        d = self._dir()
        return tuple(dir_signature(d)) if d else ()
