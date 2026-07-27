"""Evidence-bearing lifecycle for recalled Experiences and learned Skills."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class UsageKind(StrEnum):
    EXPERIENCE = "experience"
    SKILL = "skill"


class UsageState(StrEnum):
    """A monotonic record of how recalled memory affected a run."""

    INJECTED = "injected"
    ADOPTED = "adopted"
    EXECUTED = "executed"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"


_ALLOWED_TRANSITIONS = {
    UsageState.INJECTED: frozenset({UsageState.ADOPTED}),
    UsageState.ADOPTED: frozenset({UsageState.EXECUTED}),
    UsageState.EXECUTED: frozenset(
        {UsageState.VERIFIED_SUCCESS, UsageState.VERIFIED_FAILURE}
    ),
    UsageState.VERIFIED_SUCCESS: frozenset(),
    UsageState.VERIFIED_FAILURE: frozenset(),
}


def can_transition_usage(current: UsageState, target: UsageState) -> bool:
    """Return whether a usage transition preserves the evidence ordering."""

    return target in _ALLOWED_TRANSITIONS[current]


def require_usage_transition(current: UsageState, target: UsageState) -> None:
    """Reject skipped, reversed, or post-verification transitions."""

    if not can_transition_usage(current, target):
        raise ValueError(f"invalid usage transition: {current.value} -> {target.value}")


def require_adoption_evidence(
    adopted_via: str, evidence: Mapping[str, Any]
) -> None:
    """Require a durable decision reference; self-report alone is insufficient."""

    if adopted_via != "decision_trace" or not str(evidence.get("decision_id") or ""):
        raise ValueError("adoption requires decision_trace evidence")


def require_execution_evidence(evidence: Mapping[str, Any]) -> None:
    """Require an observed action match after the adoption decision."""

    evidence_ids = evidence.get("evidence_ids")
    if evidence.get("action_match") is not True or not isinstance(
        evidence_ids, (list, tuple)
    ) or not evidence_ids:
        raise ValueError("execution requires matching action evidence")


def require_verification_evidence(
    status: UsageState, evidence: Mapping[str, Any]
) -> None:
    """Require an adapter-produced terminal result."""

    if status not in {
        UsageState.VERIFIED_SUCCESS,
        UsageState.VERIFIED_FAILURE,
    }:
        raise ValueError("verification status must be terminal")
    if not str(evidence.get("adapter") or ""):
        raise ValueError("verification requires adapter evidence")
