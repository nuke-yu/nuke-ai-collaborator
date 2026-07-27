"""Deterministic Run to Case assembly; no model calls."""
from __future__ import annotations
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass

from memory.domain import OutcomeStatus, evaluate_outcome_verdict


@dataclass(frozen=True)
class OutcomeEvaluation:
    classification: str
    information_gain: str
    should_distill: bool
    confidence: float


def evaluate_outcome(
    *,
    outcome: str,
    errors: list[str],
    attempts: int,
    outcome_status: str = OutcomeStatus.UNVERIFIED_COMPLETION.value,
    correction_evidence: dict | None = None,
) -> OutcomeEvaluation:
    if outcome != "completed":
        return OutcomeEvaluation("failed", "high", False, 0.9)
    if outcome_status == OutcomeStatus.VERIFIED_FAILURE.value:
        return OutcomeEvaluation("failed", "high", False, 1.0)
    if (
        outcome_status == OutcomeStatus.VERIFIED_SUCCESS.value
        and correction_evidence
    ):
        return OutcomeEvaluation("corrected_success", "high", True, 0.9)
    if outcome_status == OutcomeStatus.VERIFIED_SUCCESS.value:
        return OutcomeEvaluation("ordinary_success", "low", False, 1.0)
    return OutcomeEvaluation("unverified_completion", "low", False, 0.5)


def task_signature(task: str) -> str:
    normalized = re.sub(r"\s+", " ", (task or "").strip().lower())[:1000]
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]


async def assemble_case(*, run_id: str, group_id: int | None, bot_id: int | None,
                        task: str, outcome: str, tool_records: list[dict]) -> str | None:
    if group_id is None or not run_id:
        return None
    from ai.memory import _memory_db
    tools, files, errors = [], [], []
    for record in tool_records:
        name = str(record.get("name") or "")
        if name and name not in tools:
            tools.append(name)
        args = record.get("args") or {}
        for key in ("path", "file_path"):
            value = args.get(key) if isinstance(args, dict) else None
            if value and value not in files:
                files.append(str(value))
        if record.get("is_error"):
            errors.append(str(record.get("result") or "")[:1000])
    verdict = evaluate_outcome_verdict(
        terminal_outcome=outcome,
        tool_records=tool_records,
    )
    correction: dict = {}
    if verdict.correction is not None:
        correction = asdict(verdict.correction)
        correction["corrective_actions"] = [
            {
                "adapter": verdict.signals[index].adapter,
                "target": verdict.signals[index].target,
                "evidence": dict(verdict.signals[index].evidence),
            }
            for index in verdict.correction.corrective_signal_indices
        ]
    signals = [verdict.status.value]
    signals.extend(
        f"{signal.adapter}:{'success' if signal.success else 'failure'}"
        for signal in verdict.signals
    )
    if correction:
        signals.append("corrected_success")
    case_id, now = f"case:{run_id}", int(time.time() * 1000)
    summary = (
        f"{verdict.status.value}; {len(tool_records)} tool attempts; "
        f"{len(errors)} errors"
    )
    async with await _memory_db("agent_cases", group_id, write=True) as db:
        await db.execute("""INSERT INTO agent_cases
          (case_id,run_id,group_id,bot_id,task,task_signature,tools_used,files_touched,attempts,
           errors,outcome,outcome_confidence,outcome_status,verification_adapter,
           correction_evidence_json,verification_signals,summary,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(run_id) DO UPDATE SET outcome=excluded.outcome,
          outcome_confidence=excluded.outcome_confidence,
          outcome_status=excluded.outcome_status,
          verification_adapter=excluded.verification_adapter,
          correction_evidence_json=excluded.correction_evidence_json,
          tools_used=excluded.tools_used,
          files_touched=excluded.files_touched, attempts=excluded.attempts, errors=excluded.errors,
          verification_signals=excluded.verification_signals, summary=excluded.summary,
          updated_at=excluded.updated_at""",
          (case_id,run_id,group_id,bot_id,task[:4000],task_signature(task),json.dumps(tools),
           json.dumps(files),len(tool_records),json.dumps(errors),outcome,verdict.confidence,
           verdict.status.value,verdict.primary_adapter,json.dumps(correction),
           json.dumps(signals),summary,now,now))
        await db.commit()
    return case_id
