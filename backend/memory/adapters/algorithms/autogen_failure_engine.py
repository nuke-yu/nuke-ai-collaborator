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
from typing import Any, Awaitable, Callable, Mapping, Sequence


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


@dataclass(frozen=True, slots=True)
class RetryResult:
    succeeded: bool
    attempts: int
    response: Any
    insights: tuple[FailureInsight, ...]


AUTOGEN_FAILURE_SYSTEM_PROMPT = """You are an AutoGen Task-Centric Failure Reflection Agent.
Analyze the user's failed task execution trace, tool execution logs, and error outputs.
Diagnose the root cause category and formulate a precise, actionable corrective insight.

Categories:
- path_not_found
- invalid_argument
- permission_denied
- timeout
- syntax_error
- execution_error
- unknown_failure

Return ONLY a JSON object:
{
  "category": "path_not_found" | "invalid_argument" | "permission_denied" | "timeout" | "syntax_error" | "execution_error" | "unknown_failure",
  "insight_summary": "Detailed root cause explanation",
  "corrective_action": "Specific actionable step to prevent this failure",
  "relevancy_score": 0.95
}
"""


class AutoGenFailureEngine:
    """Audit-grade AutoGen failure analysis and corrective insight engine (Supports LLM Prompt & Rule Fallback)."""

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

    async def analyze_failure_with_llm(
        self,
        task: str,
        errors: Sequence[str],
        tool_records: Sequence[Mapping[str, Any]] = (),
        ai_call_fn: Any = None,
        model: str = "deepseek-chat",
        provider: str = "deepseek",
    ) -> FailureInsight:
        """Perform AutoGen Failure Reflection LLM Prompt diagnosis with rule-based fallback."""
        combined_text = "\n".join(errors) if errors else ""
        for rec in tool_records:
            if rec.get("is_error"):
                combined_text += f"\n{rec.get('name')}: {rec.get('result')}"

        if not combined_text.strip():
            return self.analyze_failure(task, errors, tool_records)

        prompt = (
            f"Failed Task: {task}\n\n"
            f"Error Traces & Tool Outputs:\n{combined_text[:3000]}\n\n"
            "Diagnose root cause and return JSON with category, insight_summary, corrective_action, relevancy_score."
        )

        try:
            if ai_call_fn is None:
                from ai.client import call_ai_once
                ai_call_fn = call_ai_once

            res = await ai_call_fn(
                AUTOGEN_FAILURE_SYSTEM_PROMPT,
                [{"role": "user", "content": prompt}],
                provider=provider,
                model=model,
                temperature=0.1,
            )
            content = res.get("content", "") if isinstance(res, dict) else str(res)
            import json
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                raise ValueError("No JSON object found in LLM output")
            obj = json.loads(match.group(0))

            cat_str = str(obj.get("category", "execution_error")).lower()
            try:
                cat = FailureCategory(cat_str)
            except ValueError:
                cat = FailureCategory.EXECUTION_ERROR

            return FailureInsight(
                category=cat,
                insight_summary=str(obj.get("insight_summary", "LLM failure diagnosis")),
                corrective_action=str(obj.get("corrective_action", "Follow suggested corrective step")),
                relevancy_score=float(obj.get("relevancy_score", 0.95)),
            )
        except Exception:
            pass

        return self.analyze_failure(task, errors, tool_records)

    async def run_with_retry(
        self,
        task: str,
        attempt_fn: Callable[[str, tuple[FailureInsight, ...]], Awaitable[Any]],
        validate_fn: Callable[[Any], Awaitable[bool]],
        *,
        max_retries: int = 2,
        ai_call_fn: Any = None,
    ) -> RetryResult:
        """Execute AutoGen's failure→insight→retry→validate loop.

        ``attempt_fn`` receives the original task and accumulated insights;
        it owns tool execution. ``validate_fn`` is the authoritative success
        gate. No response is stored as a lesson until validation succeeds or
        the retry budget is exhausted.
        """
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        insights: list[FailureInsight] = []
        response: Any = None
        for attempt in range(max_retries + 1):
            response = await attempt_fn(task, tuple(insights))
            if await validate_fn(response):
                return RetryResult(True, attempt + 1, response, tuple(insights))
            if attempt >= max_retries:
                break
            errors = [str(response)[:2000]]
            insight = await self.analyze_failure_with_llm(
                task,
                errors,
                ai_call_fn=ai_call_fn,
            )
            insights.append(insight)
        return RetryResult(False, max_retries + 1, response, tuple(insights))
