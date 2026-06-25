from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from workspace import layout
from . import constants as C


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


def parse_descriptor(s: str):
    parts = s.split(":")
    kind = parts[0]
    try:
        if kind == "system":
            return SystemScope()
        if kind == "group":
            return GroupScope(int(parts[1]))
        if kind == "role":
            return RoleScope(int(parts[1]), parts[2])
        if kind == "template":
            return TemplateScope(parts[1], parts[2])
        if kind == "bot":
            return BotScope(int(parts[1]), int(parts[2]))
    except (IndexError, ValueError) as e:
        raise ValueError(f"bad scope descriptor: {s!r}") from e
    raise ValueError(f"unknown scope kind: {kind!r}")
