"""Transport-neutral Memory module contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

from memory.domain import MemoryScope

CONTRACT_VERSION = "memory.v1"


class MemoryOperationError(RuntimeError):
    """A public, transport-safe failure raised by a Memory use case."""


@dataclass(frozen=True, slots=True)
class CreatePersonalRecord:
    scope: MemoryScope
    kind: str
    content: str
    source_type: str = "manual"
    source_id: str = ""
    speaker: str = ""
    sensitivity: str = "private"


@dataclass(frozen=True, slots=True)
class CreatePersonalProjection:
    scope: MemoryScope
    record_id: str
    target_group_id: int
    target_bot_id: int | None = None
    purpose: str = "assistant_context"
    expires_at: int | None = None

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("record_id is required")
        if (not isinstance(self.target_group_id, int)
                or isinstance(self.target_group_id, bool) or self.target_group_id <= 0):
            raise ValueError("target_group_id must be positive")
        if self.target_bot_id is not None and (
            not isinstance(self.target_bot_id, int)
            or isinstance(self.target_bot_id, bool) or self.target_bot_id <= 0
        ):
            raise ValueError("target_bot_id must be positive")


@dataclass(frozen=True, slots=True)
class ObserveMemory:
    scope: MemoryScope
    source_id: str
    content: str
    source_type: str = "conversation"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if not self.content.strip():
            raise ValueError("content is required")


@dataclass(frozen=True, slots=True)
class RecallMemory:
    scope: MemoryScope
    query: str
    limit: int = 10
    token_budget: int | None = None
    kinds: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query is required")
        if self.limit < 1:
            raise ValueError("limit must be positive")
        if self.token_budget is not None and self.token_budget < 1:
            raise ValueError("token_budget must be positive")


@dataclass(frozen=True, slots=True)
class ForgetMemory:
    scope: MemoryScope
    record_ids: tuple[str, ...] = ()
    reason: str = "user_request"
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class MemoryHit:
    record_id: str
    kind: str
    content: str
    score: float
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecallResult:
    hits: Sequence[MemoryHit] = ()
    rendered_context: str = ""
    algorithm_trace: Sequence[Mapping[str, Any]] = ()
    degraded: bool = False


class MemoryEventType(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UPDATED = "updated"
    DEPRECATED = "deprecated"
    PROMOTED = "promoted"
    FORGOTTEN = "forgotten"


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    event_id: str
    event_type: MemoryEventType
    scope: MemoryScope
    record_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    contract_version: str = CONTRACT_VERSION
