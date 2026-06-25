from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, runtime_checkable

SkillEntry = Dict[str, Any]


@dataclass(frozen=True)
class ScanCtx:
    bot_id: int
    group_id: int | None = None
    role: str | None = None


@runtime_checkable
class SkillSource(Protocol):
    """One layer's read side: knows only how to enumerate its own skills and
    fingerprint its own files. Knows nothing about merging or other layers."""

    layer: str

    def enumerate(self) -> List[SkillEntry]: ...

    def signature(self) -> tuple: ...
