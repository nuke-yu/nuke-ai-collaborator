"""Evidence-bearing lifecycle for recalled Experiences and learned Skills."""
from __future__ import annotations

from enum import StrEnum


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
