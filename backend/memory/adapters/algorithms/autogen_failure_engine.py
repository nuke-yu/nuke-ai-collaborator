"""AutoGen Failure Insight Learning & Diagnosis Engine (MIT ported algorithm).

Ported from AutoGen (MIT) Task-Centric Memory and Failure Learning:
- Analyze failed execution traces and tool error outputs.
- Classify root cause categories (path errors, syntax errors, permission denied, etc.).
- Formulate actionable corrective insights to prevent repeated failures.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence


class FailureCategory(StrEnum):
    PATH_NOT_FOUND = "path_not_found"
    INVALID_ARGUMENT = "invalid_argument"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    SYNTAX_ERROR = "syntax_error"
    EXECUTION_ERROR = "execution_error"
    UNKNOWN = "unknown_failure"


@dataclass(frozen=True, slots=True)
class FailureInsight:
    category: FailureCategory
    insight_summary: str
    corrective_action: str
    relevancy_score: float = 0.9


class AutoGenFailureEngine:
    """Audit-grade AutoGen failure analysis and corrective insight engine."""

    _PATH_RE = re.compile(
        r"(file\s*not\s*found|filenotfound|no such file|does not exist|path.*invalid|cannot find)",
        re.IGNORECASE,
    )
    _ARG_RE = re.compile(
        r"(invalid argument|missing required|unexpected keyword|typeerror|jsondecodeerror)",
        re.IGNORECASE,
    )
    _PERM_RE = re.compile(
        r"(permission denied|forbidden|unauthorized|access denied|protected)",
        re.IGNORECASE,
    )
    _TIMEOUT_RE = re.compile(r"(timeout|timed out|deadline exceeded)", re.IGNORECASE)
    _SYNTAX_RE = re.compile(
        r"(syntaxerror|indentationerror|parse error|invalid syntax)", re.IGNORECASE
    )

    def analyze_failure(
        self,
        task: str,
        errors: Sequence[str],
        tool_records: Sequence[Mapping[str, Any]] = (),
    ) -> FailureInsight:
        """Analyze trace errors and derive root cause classification and corrective lesson."""
        combined_text = "\n".join(errors) if errors else ""

        # Scan for tool-level error outputs
        for rec in tool_records:
            if rec.get("is_error"):
                combined_text += f"\n{rec.get('name')}: {rec.get('result')}"

        if not combined_text.strip():
            return FailureInsight(
                category=FailureCategory.UNKNOWN,
                insight_summary="Execution failed without explicit error traces.",
                corrective_action="Inspect environment logs and retry with verbose output.",
                relevancy_score=0.5,
            )

        if self._PATH_RE.search(combined_text):
            return FailureInsight(
                category=FailureCategory.PATH_NOT_FOUND,
                insight_summary="Target file or directory path does not exist in workspace.",
                corrective_action="Verify path existence using list_dir or check relative path before accessing.",
                relevancy_score=0.95,
            )

        if self._SYNTAX_RE.search(combined_text):
            return FailureInsight(
                category=FailureCategory.SYNTAX_ERROR,
                insight_summary="Code or script contains invalid syntax or formatting errors.",
                corrective_action="Check line numbers and correct syntax or indentations before re-executing.",
                relevancy_score=0.95,
            )

        if self._PERM_RE.search(combined_text):
            return FailureInsight(
                category=FailureCategory.PERMISSION_DENIED,
                insight_summary="Operation requested restricted path or missing permissions.",
                corrective_action="Ensure operation is within workspace boundaries and proper permissions are requested.",
                relevancy_score=0.90,
            )

        if self._TIMEOUT_RE.search(combined_text):
            return FailureInsight(
                category=FailureCategory.TIMEOUT,
                insight_summary="Command or tool call exceeded time limit.",
                corrective_action="Optimize script or break long-running process into smaller async steps.",
                relevancy_score=0.90,
            )

        if self._ARG_RE.search(combined_text):
            return FailureInsight(
                category=FailureCategory.INVALID_ARGUMENT,
                insight_summary="Tool invoked with missing or malformed parameter arguments.",
                corrective_action="Validate schema types and required argument fields prior to dispatching tool.",
                relevancy_score=0.90,
            )

        return FailureInsight(
            category=FailureCategory.EXECUTION_ERROR,
            insight_summary=f"Runtime error occurred during execution: {combined_text[:150]}",
            corrective_action="Review tool parameters and handle exceptional state before retrying.",
            relevancy_score=0.80,
        )
