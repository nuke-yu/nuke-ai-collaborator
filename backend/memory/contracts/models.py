"""Transport-neutral Memory module contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

from memory.domain import MemoryRelationType, MemoryScope, UsageKind, UsageState

CONTRACT_VERSION = "memory.v1"


class MemoryOperationError(RuntimeError):
    """A public, transport-safe failure raised by a Memory use case."""


class MemoryAuthorizationError(MemoryOperationError):
    """Raised when a principal cannot perform a Memory use case."""


class LostLeaseError(MemoryOperationError):
    """Raised when a worker attempts to complete a job after losing its lease token."""


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
class IngestPersonalKnowledge:
    scope: MemoryScope
    kind: str
    statement: str
    source_type: str
    source_id: str
    speaker: str = ""
    subject: str = ""
    context_kind: str = "general"
    observed_at: int | None = None
    asserted_by_user: bool = False
    sensitivity: str = "private"


@dataclass(frozen=True, slots=True)
class ObservePersonalHabit:
    scope: MemoryScope
    habit_key: str
    statement: str
    source_type: str
    source_id: str
    context_kind: str
    observed_at: int
    polarity: str = "support"


@dataclass(frozen=True, slots=True)
class IngestGroupFact:
    scope: MemoryScope
    statement: str
    subject_key: str
    source_type: str
    source_id: str
    sensitivity: str = "group"
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("statement is required")
        if not self.subject_key.strip():
            raise ValueError("subject_key is required")
        if not self.source_id.strip():
            raise ValueError("source_id is required")


@dataclass(frozen=True, slots=True)
class RecallGroupFacts:
    scope: MemoryScope
    query: str
    limit: int = 5
    char_budget: int = 1600

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query is required")
        if self.limit < 1:
            raise ValueError("limit must be positive")
        if self.char_budget < 1:
            raise ValueError("char_budget must be positive")


@dataclass(frozen=True, slots=True)
class CreateMemoryRelation:
    scope: MemoryScope
    from_record_id: str
    to_record_id: str
    relation_type: MemoryRelationType
    source_type: str
    source_id: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    effective_from: int | None = None

    def __post_init__(self) -> None:
        if not self.from_record_id.strip():
            raise ValueError("from_record_id is required")
        if not self.to_record_id.strip():
            raise ValueError("to_record_id is required")
        if self.from_record_id == self.to_record_id:
            raise ValueError("memory relation endpoints must differ")
        if not self.source_type.strip():
            raise ValueError("source_type is required")
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if self.effective_from is not None and self.effective_from < 0:
            raise ValueError("effective_from cannot be negative")


@dataclass(frozen=True, slots=True)
class RecallMemoryRelations:
    scope: MemoryScope
    record_id: str
    relation_types: tuple[MemoryRelationType, ...] = ()

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("record_id is required")


@dataclass(frozen=True, slots=True)
class MemoryRelation:
    relation_id: str
    group_id: int
    from_record_id: str
    to_record_id: str
    relation_type: MemoryRelationType
    source_type: str
    source_id: str
    evidence: Mapping[str, Any]
    created_by: str
    effective_from: int
    valid_to: int | None
    status: str


@dataclass(frozen=True, slots=True)
class ProcessLearningCase:
    scope: MemoryScope
    case_id: str
    input_version: str = "1"

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id is required")


@dataclass(frozen=True, slots=True)
class AssembleCase:
    scope: MemoryScope
    run_id: str
    task: str
    outcome: str
    tool_records: Sequence[Mapping[str, Any]] = ()

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")


@dataclass(frozen=True, slots=True)
class RecallExperiences:
    scope: MemoryScope
    query: str
    run_id: str
    limit: int = 2
    char_budget: int = 2400

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query is required")
        if not self.run_id.strip():
            raise ValueError("run_id is required")


@dataclass(frozen=True, slots=True)
class CompleteExperienceUsage:
    scope: MemoryScope
    record_ids: tuple[str, ...]
    run_id: str
    outcome: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_attempts: int = 0

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")


@dataclass(frozen=True, slots=True)
class RecallSkills:
    scope: MemoryScope
    query: str
    run_id: str
    limit: int = 2

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query is required")
        if not self.run_id.strip():
            raise ValueError("run_id is required")


@dataclass(frozen=True, slots=True)
class CompleteSkillUsage:
    scope: MemoryScope
    skill_ids: tuple[str, ...]
    run_id: str
    outcome: str

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")


@dataclass(frozen=True, slots=True)
class MarkUsageAdopted:
    scope: MemoryScope
    kind: UsageKind
    item_ids: tuple[str, ...]
    run_id: str
    adopted_via: str
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")


@dataclass(frozen=True, slots=True)
class MarkUsageExecuted:
    scope: MemoryScope
    kind: UsageKind
    item_ids: tuple[str, ...]
    run_id: str
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")


@dataclass(frozen=True, slots=True)
class VerifyUsage:
    scope: MemoryScope
    kind: UsageKind
    item_ids: tuple[str, ...]
    run_id: str
    status: UsageState
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")


@dataclass(frozen=True, slots=True)
class FormatProjectedContext:
    scope: MemoryScope
    purpose: str = "assistant_context"
    char_budget: int = 3000


@dataclass(frozen=True, slots=True)
class EnqueuePipelineJob:
    scope: MemoryScope
    job_type: str
    input_id: str
    input_version: str = "1"

    def __post_init__(self) -> None:
        if not self.job_type.strip():
            raise ValueError("job_type is required")
        if not self.input_id.strip():
            raise ValueError("input_id is required")


@dataclass(frozen=True, slots=True)
class ClaimPipelineJob:
    scope: MemoryScope
    job_id: str
    lease_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id is required")


@dataclass(frozen=True, slots=True)
class CompletePipelineJob:
    scope: MemoryScope
    job_id: str
    output_json: str = "{}"

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id is required")


@dataclass(frozen=True, slots=True)
class FailPipelineJob:
    scope: MemoryScope
    job_id: str
    error_message: str

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id is required")



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
