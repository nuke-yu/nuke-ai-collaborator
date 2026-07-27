"""Pure Memory domain types and invariants."""

from .scope import MemoryScope, Principal, ScopeKind
from .usage import (
    UsageKind,
    UsageState,
    can_transition_usage,
    require_adoption_evidence,
    require_execution_evidence,
    require_usage_transition,
    require_verification_evidence,
)

__all__ = [
    "MemoryScope",
    "Principal",
    "ScopeKind",
    "UsageKind",
    "UsageState",
    "can_transition_usage",
    "require_adoption_evidence",
    "require_execution_evidence",
    "require_usage_transition",
    "require_verification_evidence",
]
