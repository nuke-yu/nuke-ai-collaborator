"""Derive conservative causal Memory usage from validated execution traces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from memory.domain import (
    OutcomeStatus,
    UsageKind,
    UsageState,
    evaluate_outcome_signal,
    evaluate_outcome_verdict,
)


@dataclass(frozen=True, slots=True)
class CausalUsageEvidence:
    kind: UsageKind
    item_id: str
    memory_ref: str
    action_evidence_ids: tuple[str, ...]
    first_action_ordinal: int


def _identity(memory_ref: str) -> tuple[UsageKind, str] | None:
    if memory_ref.startswith("exp:") and not any(
        char.isspace() for char in memory_ref
    ):
        return UsageKind.EXPERIENCE, memory_ref
    if memory_ref.startswith("skill:"):
        skill_id, separator, version = memory_ref.rpartition("@v")
        if (
            separator
            and skill_id.startswith("skill:")
            and version.isdigit()
            and int(version) > 0
            and not any(char.isspace() for char in skill_id)
        ):
            return UsageKind.SKILL, skill_id
    return None


def collect_causal_usages(
    tool_records: Sequence[Mapping[str, Any]],
    allowed_refs: Sequence[str],
) -> tuple[CausalUsageEvidence, ...]:
    """Collect only allowlisted refs attached to an observed tool attempt."""

    allowed = set(allowed_refs)
    collected: dict[str, tuple[UsageKind, str, int, list[str]]] = {}
    for ordinal, record in enumerate(tool_records):
        attempt_id = str(record.get("attempt_id") or "").strip()
        if not attempt_id:
            continue
        raw_refs = record.get("memory_refs")
        if not isinstance(raw_refs, (list, tuple)):
            continue
        for memory_ref in raw_refs:
            if not isinstance(memory_ref, str) or memory_ref not in allowed:
                continue
            identity = _identity(memory_ref)
            if identity is None:
                continue
            if memory_ref not in collected:
                collected[memory_ref] = (
                    identity[0],
                    identity[1],
                    ordinal,
                    [],
                )
            evidence_ids = collected[memory_ref][3]
            if attempt_id not in evidence_ids:
                evidence_ids.append(attempt_id)

    return tuple(
        CausalUsageEvidence(
            kind=kind,
            item_id=item_id,
            memory_ref=memory_ref,
            action_evidence_ids=tuple(evidence_ids),
            first_action_ordinal=first_ordinal,
        )
        for memory_ref, (kind, item_id, first_ordinal, evidence_ids)
        in collected.items()
    )


def verification_for_usage(
    usage: CausalUsageEvidence,
    tool_records: Sequence[Mapping[str, Any]],
    *,
    terminal_outcome: str,
) -> tuple[UsageState, dict[str, Any]] | None:
    """Return terminal evidence only when verification follows the cited action."""

    verdict = evaluate_outcome_verdict(
        terminal_outcome=terminal_outcome,
        tool_records=tool_records,
    )
    if verdict.status not in {
        OutcomeStatus.VERIFIED_SUCCESS,
        OutcomeStatus.VERIFIED_FAILURE,
    }:
        return None

    verifier: tuple[int, Mapping[str, Any], Any] | None = None
    for ordinal, record in enumerate(tool_records):
        signal = evaluate_outcome_signal(record)
        if signal is not None and signal.verifies_task:
            verifier = ordinal, record, signal
    if verifier is None or verifier[0] < usage.first_action_ordinal:
        return None

    status = (
        UsageState.VERIFIED_SUCCESS
        if verdict.status is OutcomeStatus.VERIFIED_SUCCESS
        else UsageState.VERIFIED_FAILURE
    )
    return status, {
        "adapter": verifier[2].adapter,
        "signal": status.value,
        "memory_ref": usage.memory_ref,
        "verifier_attempt_id": str(
            verifier[1].get("attempt_id") or ""
        ),
    }
