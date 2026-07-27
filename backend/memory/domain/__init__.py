"""Pure Memory domain types and invariants."""

from .scope import MemoryScope, Principal, ScopeKind
from .usage import UsageState, can_transition_usage, require_usage_transition

__all__ = [
    "MemoryScope",
    "Principal",
    "ScopeKind",
    "UsageState",
    "can_transition_usage",
    "require_usage_transition",
]
