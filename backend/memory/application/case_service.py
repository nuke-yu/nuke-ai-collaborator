"""Case evaluation and deterministic case assembly services."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from memory.domain import (
    OutcomeStatus, evaluate_outcome_signal, evaluate_outcome_verdict,
    identify_task,
)


@dataclass(frozen=True)
class OutcomeEvaluation:
    classification: str
    information_gain: str
    should_distill: bool
    confidence: float


def evaluate_outcome(
    *, outcome: str, errors: list[str], attempts: int,
    outcome_status: str = OutcomeStatus.UNVERIFIED_COMPLETION.value,
    correction_evidence: dict | None = None,
) -> OutcomeEvaluation:
    if outcome != "completed":
        return OutcomeEvaluation("failed", "high", False, 0.9)
    if outcome_status == OutcomeStatus.VERIFIED_FAILURE.value:
        return OutcomeEvaluation("failed", "high", False, 1.0)
    if outcome_status == OutcomeStatus.VERIFIED_SUCCESS.value and correction_evidence:
        return OutcomeEvaluation("corrected_success", "high", True, 0.9)
    if outcome_status == OutcomeStatus.VERIFIED_SUCCESS.value:
        return OutcomeEvaluation("ordinary_success", "low", False, 1.0)
    return OutcomeEvaluation("unverified_completion", "low", False, 0.5)


def task_signature(task: str) -> str:
    return identify_task(task).exact_signature


def _safe_trace_text(value: str, limit: int) -> str:
    from memory.domain.safety import redact_memory_secrets
    redacted, _ = redact_memory_secrets(value)
    return re.sub(r"[\r\n\t<>]", " ", redacted).strip()[:limit]


def build_attempt_trace(run_id: str, tool_records: list[dict]) -> list[dict]:
    """Build the deterministic trace shape used by canonical case assembly."""
    trace: list[dict] = []
    saw_verifier_failure = False
    investigate_tools = {"read_file", "list_files", "search_files", "search_code", "web_search"}
    for ordinal, record in enumerate(tool_records):
        tool = str(record.get("name") or "")
        signal = evaluate_outcome_signal(record)
        if signal is not None and signal.verifies_task:
            phase = "verify"
        elif tool in investigate_tools:
            phase = "investigate"
        elif signal is not None and signal.adapter == "file_change" and saw_verifier_failure:
            phase = "recover"
        else:
            phase = "execute"
        args = record.get("args") if isinstance(record.get("args"), dict) else {}
        target = signal.target if signal is not None and signal.adapter != "shell_exit" else str(
            args.get("path") or args.get("file_path") or args.get("url") or ""
        )
        trace.append({
            "ordinal": ordinal,
            "step_id": str(record.get("step_id") or f"{run_id}:step:{ordinal + 1}"),
            "attempt_id": str(record.get("attempt_id") or f"{run_id}:attempt:{ordinal + 1}"),
            "phase": phase,
            "action_tool": tool,
            "action_target": _safe_trace_text(target, 500),
            "observation_status": "error" if record.get("is_error") else "success",
            "observation_summary": _safe_trace_text(str(record.get("result") or ""), 500),
            "verifier_adapter": signal.adapter if signal is not None and signal.verifies_task else "",
            "verifies_task": int(signal is not None and signal.verifies_task),
        })
        if signal is not None and signal.verifies_task and not signal.success:
            saw_verifier_failure = True
    return trace


async def assemble_case(*, run_id: str, group_id: int | None, bot_id: int | None,
                        task: str, outcome: str, tool_records: list[dict]) -> str | None:
    from memory.application import CanonicalLearningService
    from memory.contracts import AssembleCase
    from memory.domain import MemoryScope
    if group_id is None or not run_id:
        return None
    from memory.application.context import require_learning
    return await require_learning().assemble_case(AssembleCase(
        scope=MemoryScope.group(group_id=group_id, bot_id=bot_id, actor_id=f"bot:{bot_id or 0}"),
        run_id=run_id, task=task, outcome=outcome, tool_records=tuple(tool_records),
    ))


__all__ = ["OutcomeEvaluation", "evaluate_outcome", "task_signature",
           "build_attempt_trace", "assemble_case"]
