"""EverOS Agent Case Extractor Engine (Apache-2.0 ported algorithm).

Ported from EverOS (everalgo) Case Management pipeline:
- Extract structured Agent Cases from execution traces.
- Evaluate task signatures, tool sequences, file touches, and error logs.
- Determine information gain and distillation gating criteria.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from memory.domain.outcome import OutcomeStatus, evaluate_outcome_verdict


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    classification: str
    information_gain: str
    should_distill: bool
    confidence: float


@dataclass(frozen=True, slots=True)
class ExtractedCase:
    case_id: str
    task: str
    task_signature: str
    tools_used: tuple[str, ...]
    files_touched: tuple[str, ...]
    errors: tuple[str, ...]
    outcome: str
    outcome_confidence: float
    verification_signals: tuple[str, ...]
    information_gain: str
    should_distill: bool
    summary: str
    correction_evidence: Mapping[str, Any]


class EverOSCaseEngine:
    """Audit-grade Agent Case extraction and evaluation engine."""

    def extract_case(
        self,
        run_id: str,
        task: str,
        outcome: str,
        tool_records: Sequence[Mapping[str, Any]],
    ) -> ExtractedCase:
        """Extract structured Case fields and distillation metrics from run traces."""
        tools: list[str] = []
        files: list[str] = []
        errors: list[str] = []

        for rec in tool_records:
            name = str(rec.get("name") or "")
            if name and name not in tools:
                tools.append(name)

            args = rec.get("args") or {}
            if isinstance(args, dict):
                for key in ("path", "file_path", "target_file"):
                    val = args.get(key)
                    if val and str(val) not in files:
                        files.append(str(val))

            if rec.get("is_error"):
                err_text = str(rec.get("result") or "")[:1000]
                if err_text:
                    errors.append(err_text)

        verdict = evaluate_outcome_verdict(
            terminal_outcome=outcome,
            tool_records=tool_records,
        )
        correction_evidence = (
            {
                "adapter": verdict.correction.adapter,
                "target": verdict.correction.target,
                "failure_signal_index": verdict.correction.failure_signal_index,
                "success_signal_index": verdict.correction.success_signal_index,
                "corrective_signal_indices": verdict.correction.corrective_signal_indices,
            }
            if verdict.correction is not None
            else {}
        )
        eval_result = self.evaluate_outcome(
            outcome=outcome,
            outcome_status=verdict.status,
            has_correction=bool(correction_evidence),
        )
        signals = [verdict.status.value]
        if eval_result.classification == "corrected_success":
            signals.append("corrected_success")

        sig = self.task_signature(task)
        case_id = f"case:{run_id}"
        summary = f"{outcome}; {len(tool_records)} tool attempts; {len(errors)} errors"

        return ExtractedCase(
            case_id=case_id,
            task=task[:4000],
            task_signature=sig,
            tools_used=tuple(tools),
            files_touched=tuple(files),
            errors=tuple(errors),
            outcome=outcome,
            outcome_confidence=eval_result.confidence,
            verification_signals=tuple(signals),
            information_gain=eval_result.information_gain,
            should_distill=eval_result.should_distill,
            summary=summary,
            correction_evidence=correction_evidence,
        )

    def evaluate_outcome(
        self,
        outcome: str,
        outcome_status: OutcomeStatus,
        has_correction: bool,
    ) -> CaseEvaluation:
        """Determine outcome classification and distillation gating."""
        if outcome != "completed":
            return CaseEvaluation(
                classification="failed",
                information_gain="high",
                should_distill=False,
                confidence=0.9,
            )

        if outcome_status is OutcomeStatus.VERIFIED_FAILURE:
            return CaseEvaluation(
                classification="failed",
                information_gain="high",
                should_distill=False,
                confidence=1.0,
            )

        if outcome_status is OutcomeStatus.VERIFIED_SUCCESS and has_correction:
            return CaseEvaluation(
                classification="corrected_success",
                information_gain="high",
                should_distill=True,
                confidence=0.9,
            )

        if outcome_status is OutcomeStatus.UNVERIFIED_COMPLETION:
            return CaseEvaluation(
                classification="unverified_completion",
                information_gain="low",
                should_distill=False,
                confidence=0.5,
            )

        return CaseEvaluation(
            classification="ordinary_success",
            information_gain="low",
            should_distill=False,
            confidence=0.9,
        )

    @staticmethod
    def task_signature(task: str) -> str:
        """Normalized SHA-256 task signature for deduplication and clustering."""
        normalized = re.sub(r"\s+", " ", (task or "").strip().lower())[:1000]
        return hashlib.sha256(normalized.encode()).hexdigest()[:24]
