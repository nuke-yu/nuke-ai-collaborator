from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from workspace import layout
from . import constants as C

_SAFE_SEGMENT = re.compile(r"^[\w-]+$")


def _safe_segment(seg: str) -> str:
    """Validate a descriptor path segment (role / lang). Unicode word characters
    + dash/underscore only — blocks path traversal (`..`, `/`, `\\`) out of the
    group tree while still allowing capitalized role names like 'PM' /
    'Architecture' and Chinese role names like '系统架构师'. NOTE: deliberately
    NOT skills.metadata._is_safe_name, which is lowercase-only and would reject
    those roles."""
    if not _SAFE_SEGMENT.match(seg or ""):
        raise ValueError(f"unsafe scope segment: {seg!r}")
    return seg


@dataclass(frozen=True)
class SystemScope:
    def dir(self) -> Path:
        return C.SYSTEM_SKILLS_ROOT


@dataclass(frozen=True)
class GroupScope:
    gid: int
    def dir(self) -> Path:
        return layout.group_shared_dir(self.gid) / "skills"


@dataclass(frozen=True)
class RoleScope:
    gid: int
    role: str
    def dir(self) -> Path:
        return layout.group_roles_dir(self.gid) / self.role / "skills"


@dataclass(frozen=True)
class TemplateScope:
    lang: str
    role: str
    def dir(self) -> Path:
        return layout.templates_roles_dir(self.lang) / self.role / "skills"


@dataclass(frozen=True)
class BotScope:
    gid: int
    bot_id: int
    def dir(self) -> Path:
        return layout.bot_dir(self.gid, self.bot_id) / "skills" / "manual"


@dataclass(frozen=True)
class LearnedScope:
    gid: int
    bot_id: int
    status: str = "active"
    def dir(self) -> Path:
        sub = "active" if self.status == "active" else "draft"
        return layout.bot_dir(self.gid, self.bot_id) / "skills" / "learned" / sub


@dataclass(frozen=True)
class ExternalGlobalScope:
    layer: str = "external_global"
    def dir(self) -> Path:
        return layout.external_global_skills_dir()


@dataclass(frozen=True)
class ExternalGroupScope:
    gid: int
    layer: str = "external_group"
    def dir(self) -> Path:
        return layout.group_external_skills_dir(self.gid)


def parse_descriptor(s: str):
    parts = s.split(":")
    kind = parts[0]
    try:
        if kind == "system":
            return SystemScope()
        if kind == "group":
            return GroupScope(int(parts[1]))
        if kind == "role":
            return RoleScope(int(parts[1]), _safe_segment(parts[2]))
        if kind == "template":
            return TemplateScope(_safe_segment(parts[1]), _safe_segment(parts[2]))
        if kind == "bot":
            return BotScope(int(parts[1]), int(parts[2]))
        if kind == "learned":
            status = parts[3] if len(parts) > 3 else "active"
            return LearnedScope(int(parts[1]), int(parts[2]), _safe_segment(status))
        if kind == "external_global":
            return ExternalGlobalScope()
        if kind == "external_group":
            return ExternalGroupScope(int(parts[1]))
    except (IndexError, ValueError) as e:
        raise ValueError(f"bad scope descriptor: {s!r}") from e
    raise ValueError(f"unknown scope kind: {kind!r}")
