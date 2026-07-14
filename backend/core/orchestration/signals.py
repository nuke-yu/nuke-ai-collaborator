"""
core/orchestration/signals.py — Unified WorkflowSignal type.

Defines the canonical signal schema for workflow completion and state transitions.
All orchestrators MUST use this schema when processing signals from ExecutionResult.

Signal schema:
  {
    "name": str,        # Signal name (e.g., "signal_stage_done", "signal_rework")
    "arguments": dict   # Signal arguments (e.g., {"reason": "...", "target_stage": "..."})
  }

Standard signals:
  - signal_stage_done: Task completed successfully
  - signal_rework: Task failed, needs rework or retry
"""
from typing import TypedDict


class WorkflowSignal(TypedDict):
    """Canonical workflow signal structure."""
    name: str
    arguments: dict


# Standard signal names
SIGNAL_STAGE_DONE = "signal_stage_done"
SIGNAL_REWORK = "signal_rework"


def is_signal_done(sig: dict) -> bool:
    """Check if signal indicates successful completion."""
    return sig.get("name") == SIGNAL_STAGE_DONE


def is_signal_rework(sig: dict) -> bool:
    """Check if signal indicates failure/rework needed."""
    return sig.get("name") == SIGNAL_REWORK


def has_completion_signal(signals: list[dict] | None) -> bool:
    """Check if signals list contains any completion signal (done or rework)."""
    if not signals:
        return False
    return any(is_signal_done(sig) or is_signal_rework(sig) for sig in signals)
