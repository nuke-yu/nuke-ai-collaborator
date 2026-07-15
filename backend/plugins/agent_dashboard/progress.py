"""
plugins/agent_dashboard/progress.py — Progress State Machine

Translates granular bus events (tool_call, ai_thought_start, stream_end, etc.)
into structured dashboard progress: {phase, percent, detail, status}.

The ProgressAdapter receives events from the Supervisor observer callback,
maintains per-group state machines, and pushes progress updates to
dashboard WebSocket clients via an internal queue.

Phases:
  queued → exploring → coding → testing → creating_pr → done
                                               ↘ error
                                                 ↘ stuck (detected by stuck_detector)

Percent is computed from phase base + iteration progress within the phase.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# Phase definitions with base percentages
PHASES = [
    ("queued",      0),
    ("exploring",  10),
    ("coding",     30),
    ("testing",    70),
    ("creating_pr", 90),
    ("done",      100),
]

PHASE_BASE = {name: base for name, base in PHASES}
PHASE_ORDER = [name for name, _ in PHASES]

# Tools that indicate exploration phase
_EXPLORE_TOOLS = {
    "read_file", "read_local_file", "list_workspace", "search",
    "code_intel", "memory_search",
}

# Tools that indicate coding phase
_CODE_TOOLS = {
    "write_file", "write_local_file", "edit_file", "edit_anchored",
    "make_dir", "run_skill",
}

# Shell commands that indicate testing
_TEST_KEYWORDS = {"pytest", "npm test", "npm run test", "jest", "vitest", "go test", "cargo test"}

TERMINAL_STATUSES = frozenset({"done", "error", "aborted", "stuck_permanently"})

_PERSISTED_STATUS = {
    "running": "running",
    "paused": "paused",
    "retrying": "retrying",
    "stuck": "stuck",
    "stuck_permanently": "stuck_permanently",
    "done": "completed",
    "error": "failed",
    "aborted": "aborted",
}
_LOCAL_STATUS = {
    "created": "running",
    "dispatched": "running",
    "running": "running",
    "restarted": "running",
    "paused": "paused",
    # A process restart means no retry coroutine is still owned locally. Resume
    # monitoring instead of hydrating a permanently skipped "retrying" state.
    "retrying": "running",
    "stuck": "stuck",
    "stuck_permanently": "stuck_permanently",
    "completed": "done",
    "failed": "error",
    "aborted": "aborted",
}
_ACTIVITY_EVENTS = frozenset({
    "ai_thought_start", "tool_call", "tool_result", "tool_progress_running",
    "tool_progress_end", "stream_end",
})
_PR_URL_RE = re.compile(r"https://github\.com/[^\s，,)]+/pull/\d+")


def _timestamp_epoch(value, fallback: float) -> float:
    try:
        text = str(value).replace("Z", "+00:00").replace(" ", "T")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return fallback


@dataclass
class TaskProgress:
    """Structured progress state for a single coding agent task."""
    group_id: int
    task_id: str = ""
    phase: str = "queued"
    percent: int = 0
    detail: str = ""
    status: str = "running"       # running | paused | error | done | stuck | aborted
    iteration: int = 0
    max_iter: int = 100
    elapsed_sec: float = 0.0
    started_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)
    files_modified: list = field(default_factory=list)
    tools_called: list = field(default_factory=list)
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "task_id": self.task_id,
            "phase": self.phase,
            "percent": self.percent,
            "detail": self.detail,
            "status": self.status,
            "iteration": self.iteration,
            "max_iter": self.max_iter,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "started_at": self.started_at,
            "files_modified": self.files_modified[-20:],  # cap at 20
            "tools_called": self.tools_called[-50:],       # cap at 50
            "error_message": self.error_message,
        }


class ProgressAdapter:
    """Subscribes to Supervisor broadcast events and maintains per-group progress state.

    Thread-safety: This class is called from the Supervisor's event loop (sync callback).
    All state mutations happen in a single thread. Dashboard WS clients read state
    via get_progress() which returns a snapshot copy.
    """

    def __init__(self, projector=None):
        self._states: dict[int, TaskProgress] = {}
        # Queue for pushing progress updates to dashboard WS clients
        self._update_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        # Groups being tracked as coding agent tasks
        self._active_groups: set[int] = set()
        self._cleanup_callbacks: list = []
        self._projector = projector

    def hydrate(self, records: list[dict]) -> None:
        """Rebuild dashboard state from the durable registry after restart."""
        for record in records:
            group_id = record["group_id"]
            status = _LOCAL_STATUS.get(record.get("status"), "error")
            state = TaskProgress(
                group_id=group_id,
                task_id=record["task_id"],
                status=status,
                max_iter=record.get("max_iterations", 100),
                error_message=record.get("error_message") or "",
            )
            state.started_at = _timestamp_epoch(
                record.get("created_at"), state.started_at
            )
            state.last_event_at = _timestamp_epoch(
                record.get("updated_at"), state.last_event_at
            )
            state.elapsed_sec = max(0.0, state.last_event_at - state.started_at)
            if status == "done":
                state.phase = "done"
                state.percent = 100
            self._states[group_id] = state
            if status not in TERMINAL_STATUSES:
                self._active_groups.add(group_id)

    def _project_status(self, state: TaskProgress) -> None:
        if self._projector:
            persisted = _PERSISTED_STATUS.get(state.status)
            if persisted:
                self._projector.enqueue_status(
                    state.task_id,
                    persisted,
                    state.error_message or None,
                )

    def add_cleanup_callback(self, callback) -> None:
        """Register cleanup invoked whenever a task leaves active tracking."""
        self._cleanup_callbacks.append(callback)

    def _retire(self, group_id: int) -> None:
        self._active_groups.discard(group_id)
        for callback in list(self._cleanup_callbacks):
            try:
                callback(group_id)
            except Exception:
                log.warning(
                    "agent_dashboard: task cleanup callback failed for group %d",
                    group_id,
                    exc_info=True,
                )

    def retire_if_terminal(self, group_id: int) -> None:
        state = self._states.get(group_id)
        if state and state.status in TERMINAL_STATUSES:
            self._retire(group_id)

    def set_status(
        self,
        group_id: int,
        status: str,
        *,
        detail: Optional[str] = None,
        error_message: Optional[str] = None,
        push: bool = True,
    ) -> None:
        """Apply a local status transition and enforce terminal cleanup."""
        state = self._states.get(group_id)
        if state is None:
            return
        state.status = status
        if status == "done":
            state.phase = "done"
            state.percent = 100
        if detail is not None:
            state.detail = detail
        if error_message is not None:
            state.error_message = error_message
        self._project_status(state)
        if status in TERMINAL_STATUSES:
            self._retire(group_id)
        if push:
            self._push_update(group_id, state)

    def register_task(self, group_id: int, task_id: str = "") -> TaskProgress:
        """Register a new coding agent task for progress tracking."""
        state = TaskProgress(group_id=group_id, task_id=task_id or str(group_id))
        self._states[group_id] = state
        self._active_groups.add(group_id)
        self._push_update(group_id, state)
        return state

    def remove_task(self, group_id: int) -> None:
        """Completely remove a task from tracking (e.g., on rollback)."""
        self._retire(group_id)
        self._states.pop(group_id, None)
        # No push_update since task is removed

    def mark_aborted(self, group_id: int) -> None:
        """Mark a task as aborted."""
        self.set_status(group_id, "aborted")

    def mark_succeeded(self, group_id: int) -> None:
        """Mark a task as successfully completed."""
        state = self._states.get(group_id)
        if state:
            state.phase = "done"
            state.percent = 100
            self.set_status(group_id, "done")

    def reset_for_retry(self, group_id: int) -> None:
        """Reset task state for retry (keeps tracking active)."""
        state = self._states.get(group_id)
        if state:
            self._active_groups.add(group_id)
            state.phase = "queued"
            state.percent = 0
            state.status = "running"
            state.detail = ""
            state.iteration = 0
            state.files_modified = []
            state.tools_called = []
            state.error_message = ""
            state.started_at = time.time()
            state.last_event_at = time.time()
            self._push_update(group_id, state)

    def get_progress(self, group_id: int) -> Optional[dict]:
        """Get current progress snapshot for a group."""
        state = self._states.get(group_id)
        return state.to_dict() if state else None

    def get_all_active(self) -> list[dict]:
        """Get progress for all active tasks."""
        return [
            self._states[gid].to_dict()
            for gid in self._active_groups
            if gid in self._states
        ]

    @property
    def update_queue(self) -> asyncio.Queue:
        """Queue that receives progress update dicts. Dashboard WS consumers read from this."""
        return self._update_queue

    # ── Event handler (called by Supervisor observer) ─────────────────

    def on_event(self, group_id: int, payload: dict) -> None:
        """Process a broadcast event. Must be non-blocking (sync, fast)."""
        if group_id not in self._active_groups:
            return

        state = self._states.get(group_id)
        if not state:
            return

        state.last_event_at = time.time()
        state.elapsed_sec = state.last_event_at - state.started_at

        event_type = payload.get("type", "")
        if event_type in _ACTIVITY_EVENTS:
            if state.status == "paused":
                self.set_status(group_id, "running", push=False)
            elif state.status == "running":
                self._project_status(state)
        handler = self._HANDLERS.get(event_type)
        if handler:
            handler(self, state, payload)
            if group_id in self._active_groups:
                self._push_update(group_id, state)

    # ── Event handlers ────────────────────────────────────────────────

    def _handle_thought_start(self, state: TaskProgress, payload: dict) -> None:
        state.iteration = payload.get("iteration", state.iteration)
        state.detail = f"AI 思考中（第 {state.iteration} 轮）"

    def _handle_tool_call(self, state: TaskProgress, payload: dict) -> None:
        tool = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", "")
        state.tools_called.append(tool)

        # Phase detection by tool type
        if tool in _EXPLORE_TOOLS:
            self._advance_phase(state, "exploring")
            state.detail = f"读取 {self._extract_filename(tool_input)}"
        elif tool in _CODE_TOOLS:
            self._advance_phase(state, "coding")
            filename = self._extract_filename(tool_input)
            state.detail = f"编辑 {filename}"
            if filename and filename not in state.files_modified:
                state.files_modified.append(filename)
        elif tool == "run_shell":
            cmd = str(tool_input) if not isinstance(tool_input, dict) else str(tool_input.get("cmd", tool_input.get("command", "")))
            if any(kw in cmd.lower() for kw in _TEST_KEYWORDS):
                self._advance_phase(state, "testing")
                state.detail = f"运行测试: {cmd[:60]}"
            else:
                state.detail = f"执行命令: {cmd[:60]}"
        elif tool == "create_pr":
            self._advance_phase(state, "creating_pr")
            state.detail = "创建 Pull Request"

    def _handle_tool_result(self, state: TaskProgress, payload: dict) -> None:
        if payload.get("error"):
            tool = payload.get("tool_name", "")
            state.detail = f"⚠️ {tool} 执行出错"
        elif payload.get("tool_name") == "create_pr" and self._projector:
            match = _PR_URL_RE.search(str(payload.get("result", "")))
            if match:
                self._projector.enqueue_pr_url(state.task_id, match.group(0))

    def _handle_tool_progress_running(self, state: TaskProgress, payload: dict) -> None:
        tool = payload.get("tool_name", "")
        message = payload.get("message", "")
        elapsed = payload.get("elapsed_sec", 0)
        state.detail = f"{tool}: {message} ({elapsed:.0f}s)"

    def _handle_tool_progress_end(self, state: TaskProgress, payload: dict) -> None:
        tool = payload.get("tool_name", "")
        duration = payload.get("duration_sec", 0)
        state.detail = f"{tool} 完成 ({duration:.1f}s)"

    def _handle_stream_end(self, state: TaskProgress, payload: dict) -> None:
        # stream_end only indicates a model output finished, NOT task completion.
        # Task completion is determined solely by WorkflowUpdate(done=True).
        # Do NOT use text heuristics to infer completion.
        if state.phase not in ("done", "error", "stuck"):
            state.detail = "Bot 回复完成"

    def _handle_stream_aborted(self, state: TaskProgress, payload: dict) -> None:
        self.set_status(state.group_id, "aborted", detail="任务已中止")

    def _handle_stream_error(self, state: TaskProgress, payload: dict) -> None:
        error = payload.get("message", "Unknown error")
        self.set_status(
            state.group_id,
            "error",
            detail=f"错误: {error[:80]}",
            error_message=error,
        )

    def _handle_workflow_update(self, state: TaskProgress, payload: dict) -> None:
        if payload.get("done"):
            self._advance_phase(state, "done")
            self.set_status(state.group_id, "done")
        elif payload.get("awaiting_confirm"):
            self.set_status(
                state.group_id, "paused", detail="等待确认", push=False
            )
        elif payload.get("active") is False:
            error = payload.get("error") or "Workflow ended without success"
            self.set_status(
                state.group_id,
                "error",
                detail=f"任务失败: {error[:80]}",
                error_message=error,
            )

    def _handle_workflow_paused(self, state: TaskProgress, payload: dict) -> None:
        reason = payload.get("reason", "")
        if reason == "done":
            self._advance_phase(state, "done")
            self.set_status(state.group_id, "done")
        elif reason in ("gate", "pause"):
            self.set_status(
                state.group_id,
                "paused",
                detail=f"暂停: {reason}",
                push=False,
            )
        elif reason in {
            "provider_unavailable",
            "completion_signal_missing",
            "rework_requested",
        }:
            defaults = {
                "provider_unavailable": "AI provider unavailable",
                "completion_signal_missing": "Agent did not report a completion signal",
                "rework_requested": "Agent requested rework",
            }
            error = payload.get("details") or defaults[reason]
            self.set_status(
                state.group_id,
                "error",
                detail=f"任务失败: {error[:80]}",
                error_message=error,
            )

    # Handler dispatch table
    _HANDLERS = {
        "ai_thought_start": _handle_thought_start,
        "tool_call": _handle_tool_call,
        "tool_result": _handle_tool_result,
        "tool_progress_running": _handle_tool_progress_running,
        "tool_progress_end": _handle_tool_progress_end,
        "stream_end": _handle_stream_end,
        "stream_aborted": _handle_stream_aborted,
        "stream_error": _handle_stream_error,
        "workflow_update": _handle_workflow_update,
        "workflow_paused": _handle_workflow_paused,
    }

    # ── Phase management ──────────────────────────────────────────────

    def _advance_phase(self, state: TaskProgress, new_phase: str) -> None:
        """Advance to a new phase (only moves forward, never backward)."""
        current_idx = PHASE_ORDER.index(state.phase) if state.phase in PHASE_ORDER else 0
        new_idx = PHASE_ORDER.index(new_phase) if new_phase in PHASE_ORDER else 0

        if new_idx > current_idx:
            state.phase = new_phase
            state.percent = self._calc_percent(state)

    def _calc_percent(self, state: TaskProgress) -> int:
        """Calculate percentage based on phase base + iteration progress."""
        base = PHASE_BASE.get(state.phase, 0)

        # Add iteration-based progress within the current phase
        phase_idx = PHASE_ORDER.index(state.phase) if state.phase in PHASE_ORDER else 0
        if phase_idx < len(PHASE_ORDER) - 1:
            next_base = PHASE_BASE[PHASE_ORDER[phase_idx + 1]]
            phase_range = next_base - base
        else:
            phase_range = 0

        # Use iteration count as fraction of phase progress (cap at 90% of phase range)
        iter_fraction = min(state.iteration / max(state.max_iter * 0.3, 1), 0.9)
        progress_in_phase = int(phase_range * iter_fraction)

        return min(base + progress_in_phase, 99)  # 100 only on "done"

    # ── Update queue ──────────────────────────────────────────────────

    def _push_update(self, group_id: int, state: TaskProgress) -> None:
        """Push a progress update to the queue (non-blocking, drop on full)."""
        try:
            self._update_queue.put_nowait({
                "type": "agent_progress",
                **state.to_dict(),
            })
        except asyncio.QueueFull:
            log.warning("agent_dashboard: progress update queue full, dropping")

    # ── Utility ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_filename(tool_input) -> str:
        """Extract filename from tool_input (string or dict)."""
        if isinstance(tool_input, dict):
            return (
                tool_input.get("file_path", "")
                or tool_input.get("path", "")
                or tool_input.get("name", "")
            )
        if isinstance(tool_input, str):
            # Could be a path string
            parts = tool_input.strip().split()
            return parts[0] if parts else ""
        return ""
