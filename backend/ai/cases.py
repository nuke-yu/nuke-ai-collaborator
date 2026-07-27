"""Deterministic Run to Case assembly; no model calls."""
from __future__ import annotations
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass

from memory.domain import (
    OutcomeStatus,
    evaluate_outcome_signal,
    evaluate_outcome_verdict,
)


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


def build_attempt_trace(run_id: str, tool_records: list[dict]) -> list[dict]:
    """Build an ordered, compact execution trace without model reasoning."""

    trace: list[dict] = []
    saw_verifier_failure = False
    investigate_tools = {
        "read_file",
        "list_files",
        "search_files",
        "search_code",
        "web_search",
    }
    for ordinal, record in enumerate(tool_records):
        tool = str(record.get("name") or "")
        signal = evaluate_outcome_signal(record)
        is_error = bool(record.get("is_error"))
        if signal is not None and signal.verifies_task:
            phase = "verify"
        elif tool in investigate_tools:
            phase = "investigate"
        elif (
            signal is not None
            and signal.adapter == "file_change"
            and saw_verifier_failure
        ):
            phase = "recover"
        else:
            phase = "execute"

        target = ""
        if signal is not None and signal.adapter != "shell_exit":
            target = signal.target
        else:
            args = record.get("args")
            if isinstance(args, dict):
                target = str(
                    args.get("path")
                    or args.get("file_path")
                    or args.get("url")
                    or ""
                )
        trace.append(
            {
                "ordinal": ordinal,
                "step_id": str(
                    record.get("step_id") or f"{run_id}:step:{ordinal + 1}"
                ),
                "attempt_id": str(
                    record.get("attempt_id")
                    or f"{run_id}:attempt:{ordinal + 1}"
                ),
                "phase": phase,
                "action_tool": tool,
                "action_target": _safe_trace_text(target, 500),
                "observation_status": "error" if is_error else "success",
                "observation_summary": _safe_trace_text(
                    str(record.get("result") or ""), 500
                ),
                "verifier_adapter": (
                    signal.adapter
                    if signal is not None and signal.verifies_task
                    else ""
                ),
                "verifies_task": int(
                    signal is not None and signal.verifies_task
                ),
            }
        )
        if signal is not None and signal.verifies_task and not signal.success:
            saw_verifier_failure = True
    return trace


def _safe_trace_text(value: str, limit: int) -> str:
    from executors.redaction import redact_secrets

    redacted, _ = redact_secrets(value)
    return re.sub(r"[\r\n\t<>]", " ", redacted).strip()[:limit]


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
    attempt_trace = build_attempt_trace(run_id, tool_records)
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
        await db.execute(
            "DELETE FROM agent_case_attempts WHERE case_id=? AND group_id=?",
            (case_id, group_id),
        )
        for attempt in attempt_trace:
            await db.execute(
                """INSERT INTO agent_case_attempts
                (case_id,ordinal,group_id,bot_id,step_id,attempt_id,phase,
                 action_tool,action_target,observation_status,
                 observation_summary,verifier_adapter,verifies_task,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    case_id,
                    attempt["ordinal"],
                    group_id,
                    bot_id,
                    attempt["step_id"],
                    attempt["attempt_id"],
                    attempt["phase"],
                    attempt["action_tool"],
                    attempt["action_target"],
                    attempt["observation_status"],
                    attempt["observation_summary"],
                    attempt["verifier_adapter"],
                    attempt["verifies_task"],
                    now,
                ),
            )
        await db.commit()
    return case_id
