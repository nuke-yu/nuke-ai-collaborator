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
import shlex
import time
from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Annotated, Optional

from api.admin_deps import require_operator
from integrations.repository_policy import DEFAULT_REPOSITORY_ADMISSION_POLICY

log = logging.getLogger(__name__)

_TEST_RUNNERS = {
    "pytest", "python", "python3", "npm", "npx", "pnpm", "yarn", "bun",
    "cargo", "go", "dotnet", "mvn", "mvnw", "gradle", "gradlew", "make",
    "bundle", "rspec", "php", "composer",
}
_SAFE_TEST_ARG = re.compile(r"^[A-Za-z0-9_./:@%+=,\-]+$")
_SHELL_CONTROL_CHARS = frozenset("|;&`$<>\n\r\0")

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
        """Admit URLs through the repository integration policy."""
        return DEFAULT_REPOSITORY_ADMISSION_POLICY.validate(v)

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
        """Accept one shell-free argv command using a known test runner."""
        command = v.strip()
        if not command:
            return ""
        if len(command) > 1000:
            raise ValueError("test_command must be at most 1000 characters")
        if any(char in _SHELL_CONTROL_CHARS for char in command):
            raise ValueError("test_command must not contain shell control characters")

        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            raise ValueError(f"test_command is not valid argv syntax: {exc}") from exc
        if not argv or len(argv) > 64:
            raise ValueError("test_command must contain 1 to 64 arguments")

        executable = argv[0].rsplit("/", 1)[-1]
        if executable not in _TEST_RUNNERS:
            raise ValueError(f"test_command runner is not allowed: {executable}")
        if executable in {"python", "python3"} and any(
            arg in {"-c", "--command"} for arg in argv[1:]
        ):
            raise ValueError("inline Python commands are not allowed")
        if any(not _SAFE_TEST_ARG.fullmatch(arg) for arg in argv):
            raise ValueError("test_command arguments contain unsupported characters")
        return command


class TaskResponse(BaseModel):
    task_id: str
    group_id: int
    status: str
    created_at: datetime


class RetryResponse(BaseModel):
    task_id: str
    status: str
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    req: CreateTaskRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
    user=Depends(require_operator),
):
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
            idempotency_key=idempotency_key,
        )

        return TaskResponse(
            task_id=record["task_id"],
            group_id=record["group_id"],
            status=record["status"],
            created_at=record["created_at"],
        )
    except Exception as e:
        from integrations.github_client import GitHubIntegrationUnavailable
        from plugins.agent_dashboard.task_store import (
            IdempotencyConflict,
            PreviousTaskCreationFailed,
            TaskCreationInProgress,
        )
        if isinstance(e, GitHubIntegrationUnavailable):
            raise HTTPException(503, str(e))
        if isinstance(e, (IdempotencyConflict, TaskCreationInProgress)):
            raise HTTPException(409, str(e))
        if isinstance(e, PreviousTaskCreationFailed):
            raise HTTPException(422, str(e))
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
