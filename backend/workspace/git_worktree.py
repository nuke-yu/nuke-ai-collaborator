"""Git Worktree Sandbox Manager.

Manages creation, promotion, and deletion of git worktrees to isolate
individual task execution from the main workspace branch.
"""
import asyncio
import logging
import os
import shutil
import threading
from pathlib import Path
import contextlib

from workspace import layout

log = logging.getLogger(__name__)


def find_nested_git_dirs(workspace_path: Path) -> list[Path]:
    """Find all nested .git directories while skipping large ignore directories (like node_modules, etc.)."""
    import os
    from workspace import _WS_IGNORE_DIRS
    git_paths = []
    if not workspace_path.exists():
        return git_paths
    workspace_resolved = workspace_path.resolve()
    for root, dirs, files in os.walk(workspace_path, followlinks=True):
        keep_dirs = []
        for d in dirs:
            if d == ".git":
                full_path = Path(root) / d
                if Path(root).resolve() != workspace_resolved:
                    git_paths.append(full_path)
            elif d not in _WS_IGNORE_DIRS:
                keep_dirs.append(d)
        dirs[:] = keep_dirs
    return git_paths

# Re-export current_workspace_path for ease of integration
from workspace.layout import current_workspace_path

# Fallback lock registry for test contexts where workspace_tools might not be imported.
_FALLBACK_LOCKS = {}
_FALLBACK_GUARD = threading.Lock()


def get_worktree_lock(group_id: int) -> asyncio.Lock:
    """Retrieve the per-group git lock to serialize git operations.

    Note on lock safety: Import success of workspace_tools is deterministic per
    Python process, guaranteeing that all call sites resolve to the same lock registry
    and prevents separate locks from being initialized for the same group.
    """
    try:
        from executors.plugins.workspace_tools import _get_worktree_lock
        return _get_worktree_lock(group_id)
    except Exception:
        with _FALLBACK_GUARD:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
            key = (loop_id, group_id)
            if key not in _FALLBACK_LOCKS:
                _FALLBACK_LOCKS[key] = asyncio.Lock()
            return _FALLBACK_LOCKS[key]


@contextlib.contextmanager
def use_worktree(group_id: int, worktree_path: Path):
    """Context manager to temporarily bind VFS and shell paths to a worktree."""
    overrides = current_workspace_path.get() or {}
    new_overrides = {**overrides, group_id: worktree_path}
    token = current_workspace_path.set(new_overrides)
    try:
        yield
    finally:
        current_workspace_path.reset(token)


async def _run_git_cmd(cwd: Path, *args: str, timeout_s: int = 30) -> str:
    """Helper to run git commands asynchronously with a timeout."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError(f"Git command timeout: git {' '.join(args)} in {cwd} exceeded {timeout_s}s")
    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()
        log.error(f"Git command failed: git {' '.join(args)} in {cwd}. Error: {err_msg}")
        raise RuntimeError(f"Git command failed: {err_msg}")
    return stdout.decode(errors="replace").strip()


def link_shared_resources(shared_dir: Path, worktree_dir: Path):
    """Symlinks shared coordination files and directories from shared/ to the worktree root.

    This ensures that resources like docs/, skills/, BOARD.md, and SPEC.md are visible
    and readable/writable in the sandboxed workspace, while keeping code isolated.
    """
    worktree_dir.mkdir(parents=True, exist_ok=True)
    try:
        for item in shared_dir.iterdir():
            if item.name in {"workspace", "group.lock"}:
                continue
            dest = worktree_dir / item.name
            if not dest.exists():
                try:
                    # Create symlink from worktree_dir pointing back to shared_dir resource
                    relative_target = os.path.relpath(item, worktree_dir)
                    dest.symlink_to(relative_target)
                    log.info(f"Symlinked shared resource: {item.name} -> {relative_target}")
                except Exception as e:
                    log.warning(f"Failed to symlink shared resource {item.name}: {e}", exc_info=True)
    except Exception as e:
        log.warning(f"Failed to list shared resources for symlinking: {e}", exc_info=True)


def link_dependencies(shared_workspace: Path, worktree_workspace: Path):
    """Recursively scans shared_workspace and symlinks node_modules, venv, and .venv

    to matching paths in worktree_workspace to avoid package reinstalls.
    """
    dependency_names = {"node_modules", "venv", ".venv", "env", ".pytest_cache", ".mypy_cache"}

    def _scan_and_link(src: Path, dest: Path, depth: int = 3):
        if depth < 0:
            return
        if not src.exists() or not src.is_dir():
            return

        try:
            for item in src.iterdir():
                if item.is_dir():
                    name = item.name
                    if name in dependency_names:
                        target = dest / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not target.exists():
                            try:
                                target.symlink_to(item.resolve())
                                log.info(f"Symlinked dependency: {target} -> {item}")
                            except Exception as e:
                                log.warning(f"Failed to symlink dependency {name} -> {item}: {e}", exc_info=True)
                    elif name not in {".git", "worktrees", "runs", ".history"}:
                        _scan_and_link(item, dest / name, depth - 1)
        except Exception as e:
            log.warning(f"Error scanning dependencies in {src}: {e}", exc_info=True)

    _scan_and_link(shared_workspace, worktree_workspace)


async def _ensure_shared_repo_clean_and_committed(shared_workspace: Path):
    """Ensure the shared workspace is a git repository and all current files are committed.

    This prevents orphaning existing files and establishes a true baseline for task worktrees.
    """
    if not (shared_workspace / ".git").exists():
        shared_workspace.mkdir(parents=True, exist_ok=True)
        await _run_git_cmd(shared_workspace, "init")
        await _run_git_cmd(shared_workspace, "config", "user.name", "Nuke AI Collaborator")
        await _run_git_cmd(shared_workspace, "config", "user.email", "collaborator@nuke.ai")

    # Add dynamic local git ignores for dependencies if not already present to prevent committing absolute symlinks
    exclude_file = shared_workspace / ".git" / "info" / "exclude"
    if exclude_file.parent.exists():
        exclude_file.touch()
        try:
            content = exclude_file.read_text(encoding="utf-8")
            patterns = {"node_modules/", "venv/", ".venv/", "env/", ".pytest_cache/", ".mypy_cache/"}
            lines_to_add = [p for p in patterns if p not in content]
            if lines_to_add:
                with open(exclude_file, "a", encoding="utf-8") as f:
                    f.write("\n" + "\n".join(lines_to_add) + "\n")
        except Exception as e:
            log.warning(f"Failed to update git exclude file: {e}", exc_info=True)

    # Commit any existing uncommitted changes in shared_workspace to form a solid baseline
    try:
        status = await _run_git_cmd(shared_workspace, "status", "--porcelain")
        if status.strip():
            has_commits = False
            try:
                await _run_git_cmd(shared_workspace, "rev-parse", "--verify", "HEAD")
                has_commits = True
            except Exception:
                pass
            
            await _run_git_cmd(shared_workspace, "add", "-A")
            commit_msg = "Initial commit" if not has_commits else "Auto-commit existing workspace state before task fork"
            await _run_git_cmd(shared_workspace, "commit", "-m", commit_msg)
            
            if not has_commits:
                try:
                    await _run_git_cmd(shared_workspace, "branch", "-M", "main")
                except Exception:
                    pass
    except Exception as e:
        log.warning(f"Failed to auto-commit shared workspace before fork: {e}", exc_info=True)


async def create_worktree(group_id: int, task_id: str, base_ref: str = "main") -> Path:
    """Create a new git worktree for a task, acquiring the group git lock.

    Sets up the worktree at workspaces/group_{group_id}/worktrees/task_{task_id}/,
    creates symlinks for shared resources and project dependencies, and returns
    the worktree directory path.
    """
    lock = get_worktree_lock(group_id)
    async with lock:
        group_dir = layout.group_dir(group_id)
        shared_dir = group_dir / "shared"
        shared_workspace = shared_dir / "workspace"

        # 1. Ensure shared repo is initialized and committed to have a real baseline
        await _ensure_shared_repo_clean_and_committed(shared_workspace)

        # Resolve base_ref dynamically if it is the default "main" to prevent failures on "master" or other branches
        if base_ref == "main" and (shared_workspace / ".git").exists():
            try:
                base_ref = await _run_git_cmd(shared_workspace, "rev-parse", "--abbrev-ref", "HEAD")
            except Exception:
                pass

        worktree_dir = group_dir / "worktrees" / f"task_{task_id}"
        worktree_workspace = worktree_dir / "workspace"
        branch_name = f"task_{task_id}"

        # 2. Return early if the worktree directory already exists (reuse it)
        if worktree_dir.exists():
            log.info(f"Reusing existing git worktree at {worktree_workspace} for task {task_id}")
            return worktree_dir

        worktree_workspace.parent.mkdir(parents=True, exist_ok=True)

        # 3. Clean up branch if it already exists to start fresh
        try:
            branches = await _run_git_cmd(shared_workspace, "branch", "--list", branch_name)
            if branches.strip():
                await _run_git_cmd(shared_workspace, "branch", "-D", branch_name)
        except Exception as e:
            log.warning(f"Failed to delete existing branch {branch_name}: {e}", exc_info=True)

        # 4. Add git worktree
        log.info(f"Adding git worktree at {worktree_workspace} for branch {branch_name} from {base_ref}")
        await _run_git_cmd(shared_workspace, "worktree", "add", "-b", branch_name, str(worktree_workspace), base_ref)

        # 5. Link shared coordination resources
        link_shared_resources(shared_dir, worktree_dir)

        # 6. Link heavy dependencies
        link_dependencies(shared_workspace, worktree_workspace)

        # 7. Copy nested git repositories from shared_workspace into worktree_workspace
        try:
            if shared_workspace.exists():
                for item in find_nested_git_dirs(shared_workspace):
                    relative_path = item.parent.relative_to(shared_workspace)
                    dest = worktree_workspace / relative_path
                    log.info(f"Hydrating nested repository into worktree: {item.parent} -> {dest}")
                    if dest.exists():
                        import shutil
                        shutil.rmtree(dest, ignore_errors=True)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copytree(str(item.parent), str(dest), symlinks=True)
        except Exception as copy_err:
            log.warning(f"Failed to hydrate nested repositories: {copy_err}", exc_info=True)

        return worktree_dir


async def _remove_worktree_nolock(group_id: int, task_id: str):
    """Inner lock-free function to remove a git worktree and delete its branch."""
    group_dir = layout.group_dir(group_id)
    shared_workspace = group_dir / "shared" / "workspace"
    worktree_dir = group_dir / "worktrees" / f"task_{task_id}"
    worktree_workspace = worktree_dir / "workspace"
    branch_name = f"task_{task_id}"

    log.info(f"Removing git worktree for task {task_id} in group {group_id}")

    # 1. git worktree remove
    if (shared_workspace / ".git").exists() and worktree_workspace.exists():
        try:
            await _run_git_cmd(shared_workspace, "worktree", "remove", "--force", str(worktree_workspace))
        except Exception as e:
            log.warning(f"git worktree remove failed for task {task_id}: {e}", exc_info=True)

    # 2. Force delete directory tree (for any leftover uncommitted files or symlinks)
    if worktree_dir.exists():
        try:
            def _onerror(func, path, exc_info):
                import stat
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass
            shutil.rmtree(worktree_dir, onerror=_onerror)
        except Exception as e:
            log.warning(f"shutil.rmtree failed for {worktree_dir}: {e}", exc_info=True)

    # 3. git branch -D
    if (shared_workspace / ".git").exists():
        try:
            await _run_git_cmd(shared_workspace, "branch", "-D", branch_name)
        except Exception as e:
            log.warning(f"Failed to delete branch {branch_name}: {e}", exc_info=True)


async def remove_worktree(group_id: int, task_id: str):
    """Force remove a git worktree and delete its branch, acquiring the group git lock."""
    lock = get_worktree_lock(group_id)
    async with lock:
        await _remove_worktree_nolock(group_id, task_id)


async def promote_worktree(group_id: int, task_id: str, target_branch: str = "main"):
    """Merge the sandboxed task changes back into the target branch, then delete the worktree.

    Acquires the group git lock. Handles merge conflicts gracefully by aborting the merge
    to prevent shared repository corruption.
    """
    lock = get_worktree_lock(group_id)
    async with lock:
        group_dir = layout.group_dir(group_id)
        shared_workspace = group_dir / "shared" / "workspace"
        
        worktree_dir = group_dir / "worktrees" / f"task_{task_id}"
        if not worktree_dir.exists():
            log.info(f"Worktree directory for task {task_id} does not exist. Skipping promotion.")
            return

        worktree_workspace = worktree_dir / "workspace"
        branch_name = f"task_{task_id}"

        if not (shared_workspace / ".git").exists():
            raise RuntimeError("No git repository in shared workspace; cannot promote changes.")

        # 1. Auto-commit any uncommitted changes inside the worktree workspace
        if worktree_workspace.exists():
            try:
                status = await _run_git_cmd(worktree_workspace, "status", "--porcelain")
                if status.strip():
                    await _run_git_cmd(worktree_workspace, "add", "-A")
                    await _run_git_cmd(worktree_workspace, "commit", "-m", f"Auto-commit changes for task {task_id} before promotion")
            except Exception as e:
                log.warning(f"Failed to auto-commit uncommitted changes in worktree {worktree_workspace}: {e}", exc_info=True)

        # 2. Ensure main is clean and committed before checkout & merge to prevent conflicts
        try:
            main_status = await _run_git_cmd(shared_workspace, "status", "--porcelain")
            if main_status.strip():
                log.info("Shared main branch has uncommitted changes. Committing them before promotion.")
                await _run_git_cmd(shared_workspace, "add", "-A")
                await _run_git_cmd(shared_workspace, "commit", "-m", "Auto-commit main branch changes before promote merge")
        except Exception as e:
            log.warning(f"Failed to clean up shared workspace main branch: {e}", exc_info=True)

        log.info(f"Merging changes from branch {branch_name} into {target_branch}")

        # Save original HEAD to restore later
        current_branch = await _run_git_cmd(shared_workspace, "rev-parse", "--abbrev-ref", "HEAD")
        if target_branch == "main" and current_branch != "main":
            target_branch = current_branch

        try:
            # Checkout target branch
            await _run_git_cmd(shared_workspace, "checkout", target_branch)
            # Merge task branch
            await _run_git_cmd(shared_workspace, "merge", branch_name, "--no-edit")
        except Exception as merge_err:
            log.error(f"Merge conflict/error occurred while merging {branch_name} into {target_branch}: {merge_err}", exc_info=True)
            # Abort merge cleanly to protect the shared repo from corruption
            try:
                await _run_git_cmd(shared_workspace, "merge", "--abort")
            except Exception:
                pass
            raise RuntimeError(f"Git merge conflict occurred while promoting task {task_id}. Merge aborted safely. Details: {merge_err}")
        finally:
            # Restore original HEAD if it was different
            if current_branch != target_branch:
                try:
                    await _run_git_cmd(shared_workspace, "checkout", current_branch)
                except Exception:
                    pass

        # 3. Copy back any nested git repositories from the worktree to the shared workspace
        # before removing the worktree.
        try:
            worktree_workspace = layout.group_dir(group_id) / "worktrees" / f"task_{task_id}" / "workspace"
            shared_workspace = layout.group_shared_dir(group_id) / "workspace"
            if worktree_workspace.exists():
                for item in find_nested_git_dirs(worktree_workspace):
                    relative_path = item.parent.relative_to(worktree_workspace)
                    dest = shared_workspace / relative_path
                    log.info(f"Promoting nested repository from worktree: {item.parent} -> {dest}")
                    if dest.exists():
                        import shutil
                        shutil.rmtree(dest, ignore_errors=True)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.move(str(item.parent), str(dest))
        except Exception as copy_err:
            log.warning(f"Failed to copy back nested repositories: {copy_err}", exc_info=True)

        # 4. Clean up the worktree directory and git branch (only reached if merge succeeds)
        await _remove_worktree_nolock(group_id, task_id)


async def prune_group_worktrees(group_id: int):
    """Prune and clean up all stale worktrees and their branches for a group on hydration."""
    lock = get_worktree_lock(group_id)
    async with lock:
        group_dir = layout.group_dir(group_id)
        shared_workspace = group_dir / "shared" / "workspace"
        worktrees_dir = group_dir / "worktrees"
        
        if not worktrees_dir.exists():
            return
        
        log.info(f"Pruning stale worktrees for group {group_id}")
        
        # Prune using git's built-in pruning command first to clear dead administrative links
        if (shared_workspace / ".git").exists():
            try:
                await _run_git_cmd(shared_workspace, "worktree", "prune")
            except Exception as e:
                log.warning(f"git worktree prune failed during startup: {e}")
        
        # Iterate through all worktree folders and cleanly remove them and their branches
        try:
            for item in list(worktrees_dir.iterdir()):
                if item.is_dir() and item.name.startswith("task_"):
                    task_id = item.name[5:]
                    await _remove_worktree_nolock(group_id, task_id)
        except Exception as e:
            log.warning(f"Error sweeping worktrees directory for group {group_id}: {e}", exc_info=True)
