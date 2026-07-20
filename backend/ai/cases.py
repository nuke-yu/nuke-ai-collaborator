"""Deterministic Run to Case assembly; no model calls."""
from __future__ import annotations
import hashlib
import json
import re
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class OutcomeEvaluation:
    classification: str
    information_gain: str
    should_distill: bool
    confidence: float


def evaluate_outcome(*, outcome: str, errors: list[str], attempts: int) -> OutcomeEvaluation:
    if outcome != "completed":
        return OutcomeEvaluation("failed", "high", False, 0.9)
    if errors:
        return OutcomeEvaluation("corrected_success", "high", True, 0.9)
    if attempts == 0:
        return OutcomeEvaluation("ordinary_success", "low", False, 0.8)
    return OutcomeEvaluation("ordinary_success", "low", False, 0.9)


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
    confidence = 1.0 if outcome == "completed" else 0.8
    signals = (["terminal_completion"] if outcome == "completed" else []) + (["tool_errors"] if errors else [])
    case_id, now = f"case:{run_id}", int(time.time() * 1000)
    summary = f"{outcome}; {len(tool_records)} tool attempts; {len(errors)} errors"
    async with await _memory_db("agent_cases", group_id, write=True) as db:
        await db.execute("""INSERT INTO agent_cases
          (case_id,run_id,group_id,bot_id,task,task_signature,tools_used,files_touched,attempts,
           errors,outcome,outcome_confidence,verification_signals,summary,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(run_id) DO UPDATE SET outcome=excluded.outcome,
          outcome_confidence=excluded.outcome_confidence, tools_used=excluded.tools_used,
          files_touched=excluded.files_touched, attempts=excluded.attempts, errors=excluded.errors,
          verification_signals=excluded.verification_signals, summary=excluded.summary,
          updated_at=excluded.updated_at""",
          (case_id,run_id,group_id,bot_id,task[:4000],task_signature(task),json.dumps(tools),
           json.dumps(files),len(tool_records),json.dumps(errors),outcome,confidence,
           json.dumps(signals),summary,now,now))
        await db.commit()
    return case_id
