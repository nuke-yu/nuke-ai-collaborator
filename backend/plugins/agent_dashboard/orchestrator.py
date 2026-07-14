"""
plugins/agent_dashboard/orchestrator.py — Task Orchestration

Wires up the coding agent task lifecycle:
  1. Create a dedicated group for the task
  2. Add a "Coding Agent" bot member
  3. Clone the target repo into the workspace
  4. Dispatch the coding agent via workflow message
  5. Track the task via ProgressAdapter

This module uses the existing group/member/workflow APIs internally,
so it doesn't duplicate any business logic.

The coding agent system prompt drives autonomous behavior:
  clone → explore code → write code → run tests → fix if failed → commit → push → create PR
"""
import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Default coding agent system prompt
CODING_AGENT_SYSTEM_PROMPT = """You are an autonomous coding agent. Your task is to implement the requested feature, test it, and submit a PR.

## Your Workflow

1. **Explore**: Read the existing codebase to understand the structure, conventions, and relevant files.
2. **Plan**: Briefly outline your implementation approach.
3. **Implement**: Write/edit the necessary files. Use edit_file for modifications, write_file only for new files.
4. **Test**: Run the test suite. If tests fail, fix the issues and re-run until all tests pass.
5. **Commit & Push**: Stage all changes, commit with a descriptive message, and push the branch.
6. **Create PR**: Create a pull request with a clear description of the changes.

## Rules

- NEVER skip testing. If the test command is provided, run it. If not, look for test configuration and run whatever test framework exists.
- If tests fail, you MUST fix them before creating a PR. Iterate up to 5 times.
- Use edit_file (not write_file) for modifying existing files — it sends only the diff.
- Keep commits focused and atomic.
- Write clean, well-documented code following the project's existing conventions.
- If you encounter an error you cannot fix after 3 attempts, describe the issue clearly and stop.

## Test Loop

When running tests:
1. Run the test command
2. If tests pass → proceed to commit
3. If tests fail → read the error output, identify the failing test, fix the code, re-run
4. Repeat until all tests pass or you've tried 5 times

## Completion (CRITICAL)

You MUST call one of these tools to signal completion:

**Success path** (all tests pass, PR created):
- Call `signal_stage_done(reason="Brief description of what was implemented and PR URL")`

**Failure path** (cannot complete after 3-5 attempts):
- Call `signal_rework(reason="Detailed description of what failed and why you cannot fix it")`

Do NOT just describe completion in text. The workflow system requires these explicit tool calls.
Without them, the task will be marked as incomplete and may be retried automatically.
"""


class TaskOrchestrator:
    """Manages the full lifecycle of coding agent tasks."""

    def __init__(self, adapter=None):
        """
        Args:
            adapter: ProgressAdapter instance for progress tracking (optional)
        """
        self._adapter = adapter
        self._tasks: dict[str, dict] = {}  # task_id → task record

    @property
    def tasks(self) -> dict:
        """Read-only access to task registry."""
        return dict(self._tasks)

    async def create_task(
        self,
        repo_url: str,
        requirements: str,
        base_branch: str = "main",
        test_command: str = "",
        github_token: Optional[str] = None,
        model: str = "deepseek-chat",
        max_iterations: int = 100,
        worker_id: Optional[str] = None,
    ) -> dict:
        """Create and dispatch a new coding agent task.

        Atomic: if any step fails, all partially created resources are cleaned up.
        Pre-flight: validates repo_url reachability before creating any resources.

        Args:
            worker_id: Pre-selected worker for this group. If provided, the group
                      is bound to this worker BEFORE dispatch, preventing the
                      dispatch-then-reassign race that cancels the just-started task.
                      If None, the group uses default modulo routing.

        Returns:
            dict with task_id, group_id, status

        Raises:
            RuntimeError: if pre-flight check fails or resource creation fails
        """
        task_id = f"agent_{uuid.uuid4().hex[:12]}"

        # Pre-flight: validate repo URL is reachable before creating any resources
        await self._preflight_check_repo(repo_url, base_branch)

        group_id = None
        try:
            # 1. Create group
            group_id = await self._create_group(task_id)

            # 2. Add coding agent bot
            bot_id = await self._add_bot(group_id, model, max_iterations)

            # 3. Clone repo into workspace
            await self._clone_repo(group_id, repo_url, base_branch, github_token or "")

            # 4. Bind group to selected worker BEFORE dispatch.
            # This prevents the race where dispatch goes to the default-routed worker,
            # then a subsequent reassign evicts that group (cancelling the just-started task).
            if worker_id:
                await self._bind_group_to_worker(group_id, worker_id)

            # 5. Register with progress adapter
            if self._adapter:
                self._adapter.register_task(group_id, task_id)

            # 6. Dispatch the coding agent
            await self._dispatch_agent(group_id, bot_id, requirements, test_command)

        except Exception as e:
            # Compensate: clean up any partially created resources
            log.error("TaskOrchestrator: create_task failed at step, rolling back group %s: %s",
                       group_id, e)
            # R2-9: Unregister from progress adapter to prevent ghost progress + stuck detector
            # from retrying a task that no longer exists.
            if self._adapter and group_id is not None:
                self._adapter.unregister_task(group_id)
            await self._rollback_group(group_id)
            raise RuntimeError(f"Task creation failed: {e}") from e

        # 6. Record task (only after all steps succeed)
        record = {
            "task_id": task_id,
            "group_id": group_id,
            "bot_id": bot_id,
            "repo_url": repo_url,
            "requirements": requirements,
            "base_branch": base_branch,
            "test_command": test_command,
            "model": model,
            "created_at": time.time(),
            "status": "dispatched",
        }
        self._tasks[task_id] = record

        log.info("TaskOrchestrator: task %s dispatched (group=%d, bot=%d)", task_id, group_id, bot_id)
        return record

    async def retry_task(self, task_id: str) -> dict:
        """Retry a stuck/failed task: abort via IPC → wait → re-dispatch.

        R2-3: Worktree cleanup is NOT done here. The Worker's _cleanup_finally()
        handles worktree promotion/removal when the task is cancelled. Cleaning
        from the Supervisor would race with the Worker's finally block.
        """
        record = self._tasks.get(task_id)
        if not record:
            raise ValueError(f"Task {task_id} not found")

        group_id = record["group_id"]

        # 1. Abort via IPC to Worker process
        await self._send_abort(group_id)

        # 2. Wait for Worker to process the abort and run its cleanup.
        # The Worker's _cleanup_finally() handles worktree promotion/removal.
        await asyncio.sleep(2.0)

        # 3. Reset progress
        if self._adapter:
            self._adapter.unregister_task(group_id)
            self._adapter.register_task(group_id, task_id)

        # 4. Re-dispatch
        await self._dispatch_agent(
            group_id,
            record["bot_id"],
            record["requirements"],
            record.get("test_command", ""),
        )

        record["status"] = "restarted"
        record["restarted_at"] = time.time()
        return record

    async def abort_task(self, task_id: str) -> dict:
        """Abort a task via IPC. Worktree cleanup handled by Worker."""
        record = self._tasks.get(task_id)
        if not record:
            raise ValueError(f"Task {task_id} not found")

        group_id = record["group_id"]

        # Abort via IPC to Worker process. Worker's _cleanup_finally() handles
        # worktree cleanup — don't do it here to avoid races.
        await self._send_abort(group_id)

        if self._adapter:
            self._adapter.unregister_task(group_id)

        record["status"] = "aborted"
        return record

    # Note: cleanup_orphan_worktrees removed (R2-2). The previous implementation
    # was dangerous: it swept for "unknown" worktrees (tid not in self._tasks),
    # but runner creates chat_<uuid> worktrees that would be incorrectly flagged
    # and deleted while still in use. Worktree lifecycle is managed by:
    #   - runner._cleanup_finally() promotes/removes on task completion
    #   - prune_group_worktrees() cleans stale worktrees on group hydration
    # These are the correct owners of worktree cleanup.

    async def _bind_group_to_worker(self, group_id: int, worker_id: str) -> None:
        """Persistently bind a group to a specific worker via assigned_worker_id.

        This must happen BEFORE dispatch so the Supervisor routes the START_WORKFLOW
        frame to the correct worker. Without this, the default modulo routing may
        send the dispatch to a different worker, and a later reassign would evict
        (and abort) the just-started task.
        """
        from db import write_connect

        async with write_connect() as db:
            await db.execute(
                "UPDATE groups SET assigned_worker_id = ? WHERE id = ?",
                (worker_id, group_id),
            )
            await db.commit()

        # Invalidate the supervisor's routing cache so the next route lookup
        # picks up the new assignment immediately.
        try:
            from runtime import supervisor as sup_mod
            sup = sup_mod.supervisor
            if sup:
                sup._routing_cache.pop(group_id, None)
        except Exception:
            pass  # best-effort; cache expires in 60s anyway

        log.info("TaskOrchestrator: bound group %d to worker %s", group_id, worker_id)

    # ── Cross-process abort + worktree cleanup ───────────────────────

    async def _send_abort(self, group_id: int) -> None:
        """Send ABORT IPC frame to the Worker process owning this group.

        Unlike calling bg.abort_group() directly (which only works in the same
        process), this sends the abort signal across the IPC boundary to the
        Worker where the actual asyncio tasks are running.
        """
        try:
            from runtime import supervisor as sup_mod
            from runtime import ipc

            sup = sup_mod.supervisor
            if sup:
                await sup.send_to_worker(
                    group_id,
                    ipc.protocol.envelope(
                        ipc.protocol.ABORT,
                        group_id=group_id,
                    ),
                )
                log.info("TaskOrchestrator: sent ABORT IPC for group %d", group_id)
        except Exception as e:
            log.warning("TaskOrchestrator: failed to send ABORT IPC for group %d: %s", group_id, e)
        except Exception as e:
            log.warning("TaskOrchestrator: worktree cleanup failed for group %d: %s", group_id, e)

    # ── Resilience: pre-flight + rollback ────────────────────────────

    async def _preflight_check_repo(self, repo_url: str, branch: str = "") -> None:
        """Validate repo URL is reachable before creating any resources.

        Uses `git ls-remote` which is lightweight (no clone) and fails fast
        on invalid URLs, auth issues, or unreachable hosts.
        """
        import asyncio

        args = ["git", "ls-remote", "--exit-code", repo_url]
        if branch:
            args.extend(["--heads", branch])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                err = stderr.decode(errors="replace").strip()
                raise RuntimeError(
                    f"Repository not reachable or branch '{branch}' not found: {err}"
                )
        except asyncio.TimeoutError:
            raise RuntimeError(f"Repository reachability check timed out: {repo_url}")

    async def _rollback_group(self, group_id: Optional[int]) -> None:
        """Clean up a partially created group: remove DB rows + workspace dir.

        Best-effort: logs errors but doesn't raise (we're already in an exception handler).
        """
        if group_id is None:
            return

        # 1. Remove workspace directory
        try:
            from workspace import layout as ws_layout
            group_dir = ws_layout.group_dir(group_id)
            if group_dir.exists():
                import shutil
                shutil.rmtree(group_dir, ignore_errors=True)
                log.info("TaskOrchestrator: rolled back workspace %s", group_dir)
        except Exception as e:
            log.warning("TaskOrchestrator: workspace rollback failed for group %d: %s", group_id, e)

        # 2. Remove group DB file
        try:
            from runtime.dbpaths import group_db_path
            db_path = group_db_path(group_id)
            if Path(db_path).exists():
                Path(db_path).unlink()
        except Exception as e:
            log.warning("TaskOrchestrator: DB file rollback failed for group %d: %s", group_id, e)

        # 3. Remove members and group row from central DB
        try:
            from db import write_connect
            async with write_connect() as db:
                await db.execute("DELETE FROM members WHERE group_id = ?", (group_id,))
                await db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
                await db.commit()
            log.info("TaskOrchestrator: rolled back DB for group %d", group_id)
        except Exception as e:
            log.warning("TaskOrchestrator: DB rollback failed for group %d: %s", group_id, e)

    # ── Internal helpers ──────────────────────────────────────────────

    async def _create_group(self, task_id: str) -> int:
        """Create a dedicated group for the coding agent task."""
        from db import write_connect

        group_name = f"Coding Agent: {task_id}"
        async with write_connect() as db:
            async with db.execute(
                "INSERT INTO groups (name, assigned_worker_id) VALUES (?, NULL)",
                (group_name,),
            ) as cur:
                group_id = cur.lastrowid
            await db.commit()

        # Initialize workspace
        from workspace import init_group_workspace
        await init_group_workspace(group_id, group_name)

        return group_id

    async def _add_bot(self, group_id: int, model: str, max_iterations: int) -> int:
        """Add a coding agent bot to the group."""
        from db import write_connect
        from workspace import init_bot_workspace
        import json

        system_prompt = CODING_AGENT_SYSTEM_PROMPT
        config = json.dumps({"max_iterations": max_iterations})

        async with write_connect() as db:
            async with db.execute(
                """INSERT INTO members (
                    group_id, name, type, role, system_prompt, avatar_color,
                    model_provider, model_name, temperature, max_tokens,
                    personality_prompt, executor_id, executor_config, done_keyword
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (group_id, "Coding Agent", "bot", "developer", system_prompt, "#10b981",
                 self._resolve_provider(model), model, 0.3, 8192,
                 None, "tool_loop_v1", config, None),
            ) as cur:
                await db.commit()
                bot_id = cur.lastrowid

        await init_bot_workspace({
            "id": bot_id,
            "group_id": group_id,
            "name": "Coding Agent",
            "role": "developer",
            "system_prompt": system_prompt,
            "personality_prompt": "",
        })

        return bot_id

    async def _clone_repo(self, group_id: int, repo_url: str, branch: str,
                          github_token: str = "") -> Path:
        """Clone the repository into the group's workspace."""
        from workspace import layout as ws_layout
        from integrations.github_client import clone_repo

        workspace = ws_layout.group_shared_dir(group_id) / "workspace"
        await clone_repo(repo_url, workspace, branch=branch, github_token=github_token)
        return workspace

    async def _dispatch_agent(self, group_id: int, bot_id: int, requirements: str, test_command: str) -> None:
        """Dispatch the coding agent via START_WORKFLOW.

        Uses the CodingAgentOrchestrator (coding_agent_v1) which provides:
          - State persistence via workflow_store (survives restart)
          - Crash recovery via resume_workflows() (auto-resume in-flight tasks)
          - WorkflowPaused events on AI failures (provider_unavailable handling)
        """
        from runtime import supervisor as sup_mod
        from runtime import ipc

        sup = sup_mod.supervisor
        if not sup:
            raise RuntimeError("Supervisor not available")

        # Send START_WORKFLOW with the coding_agent_v1 orchestrator
        body = {
            "orchestrator_id": "coding_agent_v1",
            "bot_id": bot_id,
            "requirements": requirements,
            "test_command": test_command,
        }
        await sup.send_to_worker(
            group_id,
            ipc.protocol.envelope(
                ipc.protocol.START_WORKFLOW,
                group_id=group_id,
                body=body,
            ),
        )

    @staticmethod
    def _resolve_provider(model: str) -> str:
        """Resolve model name to provider."""
        model_lower = model.lower()
        if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            return "openai"
        elif "claude" in model_lower:
            return "anthropic"
        elif "deepseek" in model_lower:
            return "deepseek"
        else:
            return "deepseek"  # default
