"""Pure Memory domain types and invariants."""

from .outcome import (
    ApiResponseAdapter,
    BuildAdapter,
    CorrectionEvidence,
    FileChangeAdapter,
    LintAdapter,
    OutcomeSignal,
    OutcomeStatus,
    OutcomeVerdict,
    PytestAdapter,
    ShellExitCodeAdapter,
    WorkflowStateAdapter,
    evaluate_outcome_verdict,
)
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
    "OutcomeSignal",
    "OutcomeStatus",
    "OutcomeVerdict",
    "Principal",
    "ApiResponseAdapter",
    "BuildAdapter",
    "CorrectionEvidence",
    "FileChangeAdapter",
    "LintAdapter",
    "PytestAdapter",
    "ShellExitCodeAdapter",
    "WorkflowStateAdapter",
    "evaluate_outcome_verdict",
    "ScopeKind",
    "UsageKind",
    "UsageState",
    "can_transition_usage",
    "require_adoption_evidence",
    "require_execution_evidence",
    "require_usage_transition",
    "require_verification_evidence",
]
