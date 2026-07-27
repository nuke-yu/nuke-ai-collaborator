"""Deterministic outcome verdicts derived from structured execution evidence."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence


class OutcomeStatus(StrEnum):
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    UNVERIFIED_COMPLETION = "unverified_completion"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class OutcomeSignal:
    adapter: str
    target: str
    success: bool
    verifies_task: bool
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class OutcomeVerdict:
    status: OutcomeStatus
    confidence: float
    primary_adapter: str
    signals: tuple[OutcomeSignal, ...]

    @property
    def is_verified(self) -> bool:
        return self.status in {
            OutcomeStatus.VERIFIED_SUCCESS,
            OutcomeStatus.VERIFIED_FAILURE,
        }


class OutcomeAdapter(Protocol):
    adapter_id: str

    def evaluate(self, record: Mapping[str, Any]) -> OutcomeSignal | None: ...


def _tool(record: Mapping[str, Any]) -> str:
    return str(record.get("name") or "").strip().lower()


def _args(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("args")
    return value if isinstance(value, Mapping) else {}


def _command(record: Mapping[str, Any]) -> str:
    args = _args(record)
    return str(args.get("cmd") or args.get("command") or "").strip()


def _result_text(record: Mapping[str, Any]) -> str:
    value = record.get("result")
    if isinstance(value, Mapping):
        return " ".join(f"{key}={item}" for key, item in sorted(value.items()))
    return str(value or "")


def _base_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "tool": _tool(record),
        "is_error": bool(record.get("is_error")),
    }
    for key in ("step_id", "attempt_id"):
        if record.get(key):
            evidence[key] = str(record[key])
    return evidence


class PytestAdapter:
    adapter_id = "pytest"
    _pattern = re.compile(r"(?:^|[\s;&|])(?:python(?:3)?\s+-m\s+)?pytest(?:\s|$)")

    def evaluate(self, record: Mapping[str, Any]) -> OutcomeSignal | None:
        command = _command(record)
        if not self._pattern.search(command.lower()):
            return None
        return OutcomeSignal(
            adapter=self.adapter_id,
            target=_pytest_target(command),
            success=not bool(record.get("is_error")),
            verifies_task=True,
            evidence={**_base_evidence(record), "command": command},
        )


class BuildAdapter:
    adapter_id = "build"
    _pattern = re.compile(
        r"(?:^|[\s;&|])(?:npm\s+run\s+build|pnpm\s+(?:run\s+)?build|"
        r"yarn\s+build|cargo\s+build|go\s+build|gradle(?:w)?\s+build|"
        r"mvn(?:w)?\s+(?:package|verify))(?:\s|$)"
    )

    def evaluate(self, record: Mapping[str, Any]) -> OutcomeSignal | None:
        command = _command(record)
        if not self._pattern.search(command.lower()):
            return None
        return OutcomeSignal(
            adapter=self.adapter_id,
            target=_normalized_command(command),
            success=not bool(record.get("is_error")),
            verifies_task=True,
            evidence={**_base_evidence(record), "command": command},
        )


class LintAdapter:
    adapter_id = "lint"
    _pattern = re.compile(
        r"(?:^|[\s;&|])(?:ruff(?:\s+check)?|eslint|flake8|mypy|pyright|"
        r"npm\s+run\s+lint|pnpm\s+(?:run\s+)?lint|yarn\s+lint)(?:\s|$)"
    )

    def evaluate(self, record: Mapping[str, Any]) -> OutcomeSignal | None:
        command = _command(record)
        if not self._pattern.search(command.lower()):
            return None
        return OutcomeSignal(
            adapter=self.adapter_id,
            target=_normalized_command(command),
            success=not bool(record.get("is_error")),
            verifies_task=True,
            evidence={**_base_evidence(record), "command": command},
        )


class ApiResponseAdapter:
    adapter_id = "api_response"
    _tools = frozenset({"api_request", "http_request", "request_url"})
    _status_pattern = re.compile(r"(?:status(?:_code)?|http)[=:\s]+(\d{3})", re.I)

    def evaluate(self, record: Mapping[str, Any]) -> OutcomeSignal | None:
        if _tool(record) not in self._tools:
            return None
        result = record.get("result")
        status: int | None = None
        if isinstance(result, Mapping):
            raw_status = result.get("status_code", result.get("status"))
            if isinstance(raw_status, int) and not isinstance(raw_status, bool):
                status = raw_status
        if status is None:
            match = self._status_pattern.search(_result_text(record))
            status = int(match.group(1)) if match else None
        if status is None:
            return None
        args = _args(record)
        target = str(args.get("url") or args.get("path") or _tool(record))
        return OutcomeSignal(
            adapter=self.adapter_id,
            target=target,
            success=200 <= status < 400 and not bool(record.get("is_error")),
            verifies_task=True,
            evidence={**_base_evidence(record), "status_code": status},
        )


class WorkflowStateAdapter:
    adapter_id = "workflow_state"
    _tools = frozenset({"signal_stage_done", "signal_rework"})

    def evaluate(self, record: Mapping[str, Any]) -> OutcomeSignal | None:
        tool = _tool(record)
        if tool not in self._tools:
            return None
        args = _args(record)
        target = str(args.get("stage") or args.get("ticket_id") or "workflow")
        success = tool == "signal_stage_done" and not bool(record.get("is_error"))
        return OutcomeSignal(
            adapter=self.adapter_id,
            target=target,
            success=success,
            verifies_task=True,
            evidence=_base_evidence(record),
        )


class FileChangeAdapter:
    adapter_id = "file_change"
    _tools = frozenset({"write_file", "edit_file", "apply_patch"})

    def evaluate(self, record: Mapping[str, Any]) -> OutcomeSignal | None:
        if _tool(record) not in self._tools:
            return None
        args = _args(record)
        target = str(args.get("path") or args.get("file_path") or "workspace")
        return OutcomeSignal(
            adapter=self.adapter_id,
            target=target,
            success=not bool(record.get("is_error")),
            verifies_task=False,
            evidence=_base_evidence(record),
        )


class ShellExitCodeAdapter:
    adapter_id = "shell_exit"
    _tools = frozenset({"run_shell", "shell"})

    def evaluate(self, record: Mapping[str, Any]) -> OutcomeSignal | None:
        if _tool(record) not in self._tools:
            return None
        command = _command(record)
        if not command:
            return None
        return OutcomeSignal(
            adapter=self.adapter_id,
            target=_normalized_command(command),
            success=not bool(record.get("is_error")),
            verifies_task=False,
            evidence={**_base_evidence(record), "command": command},
        )


DEFAULT_OUTCOME_ADAPTERS: tuple[OutcomeAdapter, ...] = (
    PytestAdapter(),
    BuildAdapter(),
    LintAdapter(),
    ApiResponseAdapter(),
    WorkflowStateAdapter(),
    FileChangeAdapter(),
    ShellExitCodeAdapter(),
)


def evaluate_outcome_verdict(
    *,
    terminal_outcome: str,
    tool_records: Sequence[Mapping[str, Any]],
    adapters: Sequence[OutcomeAdapter] = DEFAULT_OUTCOME_ADAPTERS,
) -> OutcomeVerdict:
    """Evaluate a run without treating arbitrary successful actions as proof."""

    terminal = terminal_outcome.strip().lower()
    signals: list[OutcomeSignal] = []
    for record in tool_records:
        for adapter in adapters:
            signal = adapter.evaluate(record)
            if signal is not None:
                signals.append(signal)
                break

    if terminal == "cancelled":
        return OutcomeVerdict(OutcomeStatus.CANCELLED, 1.0, "", tuple(signals))
    if terminal in {"abandoned", "timed_out"}:
        return OutcomeVerdict(OutcomeStatus.ABANDONED, 1.0, "", tuple(signals))

    task_signals = [signal for signal in signals if signal.verifies_task]
    primary = task_signals[-1] if task_signals else None
    if primary is not None:
        status = (
            OutcomeStatus.VERIFIED_SUCCESS
            if primary.success and terminal == "completed"
            else OutcomeStatus.VERIFIED_FAILURE
        )
        return OutcomeVerdict(status, 1.0, primary.adapter, tuple(signals))

    if terminal == "completed":
        return OutcomeVerdict(
            OutcomeStatus.UNVERIFIED_COMPLETION, 0.5, "", tuple(signals)
        )
    return OutcomeVerdict(OutcomeStatus.ABANDONED, 0.5, "", tuple(signals))


def _normalized_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip().lower())[:500]


def _pytest_target(command: str) -> str:
    normalized = _normalized_command(command)
    match = PytestAdapter._pattern.search(normalized)
    if match is None:
        return normalized
    suffix = normalized[match.end() :].strip()
    positional = [
        token for token in suffix.split()
        if token and not token.startswith("-")
    ]
    return "pytest:" + (" ".join(positional) if positional else "suite")
