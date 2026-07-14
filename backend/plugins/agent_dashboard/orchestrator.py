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

## Completion

When all tests pass and code is committed:
1. Push your branch
2. Create a PR with: title, description of changes, list of modified files
3. Report the PR URL as your final output
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
            await self._clone_repo(group_id, repo_url, base_branch)

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
        """Retry a stuck/failed task: abort via IPC → cleanup → re-dispatch."""
        record = self._tasks.get(task_id)
        if not record:
            raise ValueError(f"Task {task_id} not found")

        group_id = record["group_id"]

        # 1. Abort via IPC to Worker process (not direct bg.abort_group which
        # only works in the same process — the actual tasks run in Worker)
        await self._send_abort(group_id)

        # 2. Clean up ALL worktrees for this group (runner uses chat_<uuid> IDs
        # internally, not just the task_id)
        await self._cleanup_group_worktrees(group_id)

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
        """Abort and clean up a task via IPC."""
        record = self._tasks.get(task_id)
        if not record:
            raise ValueError(f"Task {task_id} not found")

        group_id = record["group_id"]

        # Abort via IPC to Worker process
        await self._send_abort(group_id)

        # Clean up all worktrees for this group
        await self._cleanup_group_worktrees(group_id)

        if self._adapter:
            self._adapter.unregister_task(group_id)

        record["status"] = "aborted"
        return record

    async def cleanup_orphan_worktrees(self) -> int:
        """Scan for and remove orphan worktrees from completed/aborted tasks.

        This is a periodic maintenance task that cleans up worktrees left behind
        by tasks that finished or were aborted without proper cleanup (e.g., due
        to a crash during the cleanup phase).

        Returns:
            Number of worktrees cleaned up.
        """
        from workspace import layout as ws_layout
        from workspace.git_worktree import remove_worktree
        import shutil

        cleaned = 0
        for task_id, record in list(self._tasks.items()):
            if record["status"] not in ("done", "aborted", "stuck_permanently"):
                continue

            group_id = record["group_id"]
            group_dir = ws_layout.group_dir(group_id)
            worktrees_dir = group_dir / "worktrees"

            if not worktrees_dir.exists():
                continue

            # Check for worktree dirs matching this task
            task_wt = worktrees_dir / f"task_{task_id}"
            if task_wt.exists():
                try:
                    await remove_worktree(group_id, task_id)
                    cleaned += 1
                    log.info("TaskOrchestrator: cleaned orphan worktree for task %s", task_id)
                except Exception as e:
                    log.warning("TaskOrchestrator: failed to clean worktree for task %s: %s", task_id, e)

        # Also scan for worktrees that don't belong to any known task
        for task_id, record in list(self._tasks.items()):
            group_id = record["group_id"]
            group_dir = ws_layout.group_dir(group_id)
            worktrees_dir = group_dir / "worktrees"

            if not worktrees_dir.exists():
                continue

            for item in list(worktrees_dir.iterdir()):
                if item.is_dir() and item.name.startswith("task_"):
                    tid = item.name[5:]
                    if tid not in self._tasks:
                        # Unknown worktree — orphan from a previous run
                        try:
                            await remove_worktree(group_id, tid)
                            cleaned += 1
                            log.info("TaskOrchestrator: cleaned unknown worktree task_%s in group %d", tid, group_id)
                        except Exception as e:
                            log.warning("TaskOrchestrator: failed to clean unknown worktree %s: %s", tid, e)

        return cleaned

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

    async def _cleanup_group_worktrees(self, group_id: int) -> None:
        """Remove ALL worktrees for a group (not just by task_id).

        Runner may create worktrees with chat_<uuid> IDs internally, so we
        sweep the entire worktrees/ directory for the group.
        """
        try:
            from workspace import layout as ws_layout
            from workspace.git_worktree import remove_worktree

            group_dir = ws_layout.group_dir(group_id)
            worktrees_dir = group_dir / "worktrees"

            if not worktrees_dir.exists():
                return

            for item in list(worktrees_dir.iterdir()):
                if item.is_dir() and item.name.startswith("task_"):
                    tid = item.name[5:]
                    try:
                        await remove_worktree(group_id, tid)
                    except Exception as e:
                        log.warning("TaskOrchestrator: failed to remove worktree %s: %s", tid, e)
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

    async def _clone_repo(self, group_id: int, repo_url: str, branch: str) -> Path:
        """Clone the repository into the group's workspace."""
        from workspace import layout as ws_layout
        from integrations.github_client import clone_repo

        workspace = ws_layout.group_shared_dir(group_id) / "workspace"
        await clone_repo(repo_url, workspace, branch=branch)
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
