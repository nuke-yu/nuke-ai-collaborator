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
import time
from dataclasses import dataclass, field, asdict
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

    def __init__(self):
        self._states: dict[int, TaskProgress] = {}
        # Queue for pushing progress updates to dashboard WS clients
        self._update_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        # Groups being tracked as coding agent tasks
        self._active_groups: set[int] = set()

    def register_task(self, group_id: int, task_id: str = "") -> TaskProgress:
        """Register a new coding agent task for progress tracking."""
        state = TaskProgress(group_id=group_id, task_id=task_id or str(group_id))
        self._states[group_id] = state
        self._active_groups.add(group_id)
        self._push_update(group_id, state)
        return state

    def unregister_task(self, group_id: int) -> None:
        """Stop tracking a task."""
        self._active_groups.discard(group_id)
        # Keep state for history but mark as inactive
        state = self._states.get(group_id)
        if state:
            state.status = "done"
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
        handler = self._HANDLERS.get(event_type)
        if handler:
            handler(self, state, payload)
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
        # stream_end from the bot means the run is finishing
        if state.phase not in ("done", "error", "stuck"):
            # Check if this looks like a final completion
            preview = payload.get("preview", "")
            if state.phase == "creating_pr":
                self._advance_phase(state, "done")
                state.status = "done"
            elif "完成" in preview or "done" in preview.lower() or "PR" in preview:
                self._advance_phase(state, "done")
                state.status = "done"
            else:
                state.detail = "Bot 回复完成"

    def _handle_stream_aborted(self, state: TaskProgress, payload: dict) -> None:
        state.status = "aborted"
        state.detail = "任务已中止"

    def _handle_stream_error(self, state: TaskProgress, payload: dict) -> None:
        state.status = "error"
        state.error_message = payload.get("message", "Unknown error")
        state.detail = f"错误: {state.error_message[:80]}"

    def _handle_workflow_update(self, state: TaskProgress, payload: dict) -> None:
        if payload.get("done"):
            self._advance_phase(state, "done")
            state.status = "done"
        elif payload.get("awaiting_confirm"):
            state.status = "paused"
            state.detail = "等待确认"

    def _handle_workflow_paused(self, state: TaskProgress, payload: dict) -> None:
        reason = payload.get("reason", "")
        if reason == "done":
            self._advance_phase(state, "done")
            state.status = "done"
        elif reason in ("gate", "pause"):
            state.status = "paused"
            state.detail = f"暂停: {reason}"
        elif reason == "provider_unavailable":
            state.status = "error"
            state.error_message = payload.get("details", "AI provider unavailable")

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
