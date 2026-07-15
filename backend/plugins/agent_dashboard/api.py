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
import re
import time
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from api.admin_deps import require_operator

log = logging.getLogger(__name__)

router = APIRouter()

# Module-level reference to shared state (set by __init__.register)
_adapter = None
_host = None
_stuck_detector = None
_orchestrator = None


def set_context(adapter, host, stuck_detector, orchestrator=None):
    """Called by plugin __init__ to inject dependencies."""
    global _adapter, _host, _stuck_detector, _orchestrator
    _adapter = adapter
    _host = host
    _stuck_detector = stuck_detector
    _orchestrator = orchestrator


# ── Request/Response Models ───────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    repo_url: str = Field(..., description="Git repository URL to clone")
    requirements: str = Field(..., description="Task requirements / feature description")
    base_branch: str = Field("main", description="Base branch to work from")
    test_command: str = Field("", description="Test command to run (e.g. 'pytest -x')")
    model: str = Field("deepseek-chat", description="AI model to use")
    max_iterations: int = Field(100, description="Max tool loop iterations", ge=1, le=500)

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, v: str) -> str:
        """Only allow HTTPS git URLs from github.com (P0-4: GitHub-only for now)."""
        allowed_pattern = re.compile(
            r'^https://github\.com/[\w\-./]+\.git$'
        )
        if not allowed_pattern.match(v):
            raise ValueError(
                "repo_url must be an HTTPS git URL from github.com "
                "(GitLab/Bitbucket not yet supported)"
            )
        return v

    @field_validator("requirements")
    @classmethod
    def validate_requirements(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("requirements must be at least 10 characters")
        if len(v) > 10000:
            raise ValueError("requirements must be under 10000 characters")
        return v

    @field_validator("test_command")
    @classmethod
    def validate_test_command(cls, v: str) -> str:
        """Block dangerous shell constructs in test commands."""
        dangerous = ["|", ";", "&&", "||", "`", "$(", "> ", "< ", "curl", "wget", "eval", "bash"]
        for d in dangerous:
            if d in v:
                raise ValueError(f"test_command contains disallowed construct: {d}")
        return v


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
async def create_task(req: CreateTaskRequest, user=Depends(require_operator)):
    """Create a new coding agent task.

    Uses the TaskOrchestrator to: create group → add bot → clone repo → dispatch agent.
    Returns the task_id and group_id for tracking.
    """
    if not _orchestrator:
        raise HTTPException(503, "Agent orchestrator not initialized")

    try:
        # Pick least-loaded worker BEFORE creating the task.
        # The worker_id is passed to create_task so the group is bound to this
        # worker before dispatch, preventing the dispatch-then-reassign race
        # where eviction cancels the just-started task.
        worker_id = None
        if _host:
            worker_id = _host.pick_worker("least_loaded")

        record = await _orchestrator.create_task(
            repo_url=req.repo_url,
            requirements=req.requirements,
            base_branch=req.base_branch,
            test_command=req.test_command,
            model=req.model,
            max_iterations=req.max_iterations,
            worker_id=worker_id,
        )

        return TaskResponse(
            task_id=record["task_id"],
            group_id=record["group_id"],
            status=record["status"],
            created_at=record["created_at"],
        )
    except Exception as e:
        from integrations.github_client import GitHubIntegrationUnavailable
        if isinstance(e, GitHubIntegrationUnavailable):
            raise HTTPException(503, str(e))
        log.exception("agent_dashboard: task creation failed")
        raise HTTPException(500, f"Task creation failed: {e}")


@router.get("/tasks")
async def list_tasks(user=Depends(require_operator)):
    """List all active tasks with current progress."""
    if not _orchestrator:
        return {"tasks": []}

    # P1-1: Fetch tasks from database (async property)
    tasks = await _orchestrator.tasks

    result = []
    for task_id, record in tasks.items():
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
async def get_task(task_id: str, user=Depends(require_operator)):
    """Get detailed progress for a specific task."""
    if not _orchestrator:
        raise HTTPException(404, "Task not found")

    # P1-1: Fetch tasks from database (async property)
    tasks = await _orchestrator.tasks
    record = tasks.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")

    progress = _adapter.get_progress(record["group_id"]) if _adapter else None

    return {
        "task_id": task_id,
        "group_id": record["group_id"],
        "status": record["status"],
        "created_at": record["created_at"],
        "repo_url": record.get("repo_url", ""),
        "requirements": record.get("requirements", ""),
        "progress": progress,
    }


@router.post("/tasks/{task_id}/retry", response_model=RetryResponse)
async def retry_task(task_id: str, user=Depends(require_operator)):
    """Retry a stuck or failed task via the orchestrator."""
    if not _orchestrator:
        raise HTTPException(503, "Agent orchestrator not initialized")

    try:
        record = await _orchestrator.retry_task(task_id)
        return RetryResponse(
            task_id=task_id,
            status=record["status"],
            message=f"Task {task_id} has been restarted",
        )
    except ValueError:
        raise HTTPException(404, "Task not found")
    except Exception as e:
        log.exception("agent_dashboard: retry failed for %s", task_id)
        raise HTTPException(500, f"Retry failed: {e}")


@router.delete("/tasks/{task_id}")
async def abort_task(task_id: str, user=Depends(require_operator)):
    """Abort and clean up a task via the orchestrator."""
    if not _orchestrator:
        raise HTTPException(503, "Agent orchestrator not initialized")

    try:
        record = await _orchestrator.abort_task(task_id)
        return {"task_id": task_id, "status": record["status"]}
    except ValueError:
        raise HTTPException(404, "Task not found")


@router.get("/workers")
async def get_workers(user=Depends(require_operator)):
    """Get worker load stats for monitoring and routing decisions."""
    if not _host:
        return {"workers": {}}

    stats = _host.get_worker_stats()
    result = {}
    for wid, s in stats.items():
        bg = s.get("bg", {})
        result[wid] = {
            "active_tasks": bg.get("active_tasks", 0),
            "groups_with_active_tasks": bg.get("groups_with_active_tasks", 0),
            "tasks_by_group": bg.get("tasks_by_group", {}),
        }
    return {"workers": result}
