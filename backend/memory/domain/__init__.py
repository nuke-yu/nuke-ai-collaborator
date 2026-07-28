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
    evaluate_outcome_signal,
    evaluate_outcome_verdict,
)
from .ownership import (
    FactAdmission,
    FactAuthority,
    FactSensitivity,
    MemoryOwnerType,
    admit_group_fact,
)
from .relations import MemoryRelationType
from .scope import MemoryScope, Principal, ScopeKind
from .task_identity import TaskIdentity, identify_task
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
    "MemoryOwnerType",
    "MemoryRelationType",
    "OutcomeSignal",
    "OutcomeStatus",
    "OutcomeVerdict",
    "Principal",
    "FactAdmission",
    "FactAuthority",
    "FactSensitivity",
    "ApiResponseAdapter",
    "BuildAdapter",
    "CorrectionEvidence",
    "FileChangeAdapter",
    "LintAdapter",
    "PytestAdapter",
    "ShellExitCodeAdapter",
    "WorkflowStateAdapter",
    "evaluate_outcome_signal",
    "evaluate_outcome_verdict",
    "ScopeKind",
    "TaskIdentity",
    "UsageKind",
    "UsageState",
    "can_transition_usage",
    "admit_group_fact",
    "identify_task",
    "require_adoption_evidence",
    "require_execution_evidence",
    "require_usage_transition",
    "require_verification_evidence",
]
