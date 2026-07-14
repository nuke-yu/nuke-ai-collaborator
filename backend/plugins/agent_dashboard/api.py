"""
plugins/agent_dashboard/api.py — REST API Endpoints

Provides REST endpoints for the dashboard frontend:
  POST   /api/agent/tasks           — Create a new coding agent task
  GET    /api/agent/tasks           — List all active tasks with progress
  GET    /api/agent/tasks/{id}      — Get progress for a specific task
  POST   /api/agent/tasks/{id}/retry — Retry a stuck/failed task
  DELETE /api/agent/tasks/{id}      — Abort and clean up a task
  GET    /api/agent/workers         — Get worker load stats (for monitoring)
"""
import logging
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

log = logging.getLogger(__name__)

router = APIRouter()

# Module-level reference to shared state (set by __init__.register)
_adapter = None
_host = None
_stuck_detector = None

# Task registry: task_id → {group_id, spec, created_at, status}
_task_registry: dict[str, dict] = {}


def set_context(adapter, host, stuck_detector):
    """Called by plugin __init__ to inject dependencies."""
    global _adapter, _host, _stuck_detector
    _adapter = adapter
    _host = host
    _stuck_detector = stuck_detector


# ── Request/Response Models ───────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    repo_url: str = Field(..., description="Git repository URL to clone")
    requirements: str = Field(..., description="Task requirements / feature description")
    base_branch: str = Field("main", description="Base branch to work from")
    test_command: str = Field("", description="Test command to run (e.g. 'pytest -x')")
    github_token: Optional[str] = Field(None, description="GitHub token for PR creation")
    model: str = Field("deepseek-chat", description="AI model to use")
    max_iterations: int = Field(100, description="Max tool loop iterations")


class TaskResponse(BaseModel):
    task_id: str
    group_id: int
    status: str
    created_at: float


class RetryResponse(BaseModel):
    task_id: str
    status: str
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/tasks", response_model=TaskResponse)
async def create_task(req: CreateTaskRequest):
    """Create a new coding agent task.

    Creates a dedicated group, clones the repo, and dispatches the coding agent.
    Returns the task_id and group_id for tracking.
    """
    # Generate task ID
    task_id = f"agent_{int(time.time())}_{len(_task_registry)}"

    # Create a dedicated group for this task
    # TODO: Implement group creation via core API
    # For now, use a placeholder group_id (will be implemented in Phase 2)
    group_id = _allocate_group_id()

    # Register task
    _task_registry[task_id] = {
        "group_id": group_id,
        "spec": req.model_dump(),
        "created_at": time.time(),
        "status": "queued",
    }

    # Register with progress adapter
    if _adapter:
        _adapter.register_task(group_id, task_id)

    # Pick least-loaded worker and assign
    if _host:
        worker_id = _host.pick_worker("least_loaded")
        if worker_id:
            await _host.reassign_group(group_id, worker_id)
            log.info("agent_dashboard: task %s assigned to worker %s", task_id, worker_id)

    # TODO: Dispatch the actual coding agent work
    # This will be implemented when integrating with the tool_loop
    log.info("agent_dashboard: task %s created for group %d", task_id, group_id)

    return TaskResponse(
        task_id=task_id,
        group_id=group_id,
        status="queued",
        created_at=_task_registry[task_id]["created_at"],
    )


@router.get("/tasks")
async def list_tasks():
    """List all active tasks with current progress."""
    result = []
    for task_id, record in _task_registry.items():
        progress = _adapter.get_progress(record["group_id"]) if _adapter else None
        result.append({
            "task_id": task_id,
            "group_id": record["group_id"],
            "status": record["status"],
            "created_at": record["created_at"],
            "progress": progress,
        })
    return {"tasks": result}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get detailed progress for a specific task."""
    record = _task_registry.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")

    progress = _adapter.get_progress(record["group_id"]) if _adapter else None

    return {
        "task_id": task_id,
        "group_id": record["group_id"],
        "status": record["status"],
        "created_at": record["created_at"],
        "spec": record["spec"],
        "progress": progress,
    }


@router.post("/tasks/{task_id}/retry", response_model=RetryResponse)
async def retry_task(task_id: str):
    """Retry a stuck or failed task.

    Steps:
      1. Abort current run
      2. Clean up worktree sandbox
      3. Reset progress state
      4. Re-dispatch the task
    """
    record = _task_registry.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")

    group_id = record["group_id"]

    # 1. Abort current run
    try:
        from core.bg import abort_group
        cancelled = abort_group(group_id)
        log.info("agent_dashboard: aborted %d tasks for group %d", cancelled, group_id)
    except Exception as e:
        log.warning("agent_dashboard: abort failed for group %d: %s", group_id, e)

    # 2. Clean up worktree
    try:
        from workspace.git_worktree import remove_worktree
        await remove_worktree(group_id, task_id)
    except Exception as e:
        log.warning("agent_dashboard: worktree cleanup failed: %s", e)

    # 3. Reset progress state
    if _adapter:
        _adapter.unregister_task(group_id)
        _adapter.register_task(group_id, task_id)

    # 4. Update task record
    record["status"] = "restarted"
    record["restarted_at"] = time.time()

    # TODO: Re-dispatch the coding agent work
    log.info("agent_dashboard: task %s restarted", task_id)

    return RetryResponse(
        task_id=task_id,
        status="restarted",
        message=f"Task {task_id} has been aborted and restarted",
    )


@router.delete("/tasks/{task_id}")
async def abort_task(task_id: str):
    """Abort and clean up a task."""
    record = _task_registry.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")

    group_id = record["group_id"]

    # Abort
    try:
        from core.bg import abort_group
        abort_group(group_id)
    except Exception:
        pass

    # Clean up worktree
    try:
        from workspace.git_worktree import remove_worktree
        await remove_worktree(group_id, task_id)
    except Exception:
        pass

    # Update state
    if _adapter:
        _adapter.unregister_task(group_id)

    record["status"] = "aborted"

    return {"task_id": task_id, "status": "aborted"}


@router.get("/workers")
async def get_workers():
    """Get worker load stats for monitoring and routing decisions."""
    if not _host:
        return {"workers": {}}

    stats = _host.get_worker_stats()
    # Enrich with load score
    result = {}
    for wid, s in stats.items():
        bg = s.get("bg", {})
        result[wid] = {
            "active_tasks": bg.get("active_tasks", 0),
            "groups_with_active_tasks": bg.get("groups_with_active_tasks", 0),
            "tasks_by_group": bg.get("tasks_by_group", {}),
        }
    return {"workers": result}


# ── Helpers ───────────────────────────────────────────────────────────

_group_counter = 10000  # Start from a high number to avoid conflicts

def _allocate_group_id() -> int:
    """Allocate a new group ID for a coding agent task.

    TODO: Replace with actual group creation via core API.
    For now, uses a counter to generate unique IDs.
    """
    global _group_counter
    _group_counter += 1
    return _group_counter
