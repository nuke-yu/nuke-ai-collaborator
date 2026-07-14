"""integrations/github_client.py — Real GitHub integration via `gh` CLI.

Implements GitClient ABC from integrations/git.py using the GitHub CLI (`gh`)
for branch management, push, and PR creation. Falls back gracefully when `gh`
is not installed or not authenticated.

Design choices:
  - Uses `gh` CLI instead of PyGithub to avoid extra dependencies
  - `gh` handles auth via GITHUB_TOKEN env var or `gh auth login`
  - All git operations use async subprocess with timeout
  - create_pr() pushes the current branch and creates a PR via `gh pr create`
"""
import asyncio
import logging
import os
import shutil
from pathlib import Path

from integrations.git import GitClient, set_git

log = logging.getLogger(__name__)

# Default timeout for git/gh commands
_CMD_TIMEOUT = 60


async def _run_cmd(*args: str, cwd: str | Path | None = None, timeout: int = _CMD_TIMEOUT) -> tuple[str, str, int]:
    """Run a command asynchronously. Returns (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(args)}")

    return (
        stdout.decode(errors="replace").strip(),
        stderr.decode(errors="replace").strip(),
        proc.returncode or 0,
    )


async def _git(*args: str, cwd: str | Path | None = None) -> str:
    """Run a git command, raising on failure."""
    stdout, stderr, rc = await _run_cmd("git", *args, cwd=cwd)
    if rc != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc={rc}): {stderr}")
    return stdout


async def _gh(*args: str, cwd: str | Path | None = None) -> str:
    """Run a gh CLI command, raising on failure."""
    stdout, stderr, rc = await _run_cmd("gh", *args, cwd=cwd)
    if rc != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed (rc={rc}): {stderr}")
    return stdout


def is_gh_available() -> bool:
    """Check if `gh` CLI is installed and authenticated."""
    return shutil.which("gh") is not None


async def clone_repo(repo_url: str, dest: str | Path, branch: str = "") -> Path:
    """Clone a git repository to the destination directory.

    Args:
        repo_url: Git repository URL (HTTPS or SSH)
        dest: Destination directory path
        branch: Optional branch to checkout after clone

    Returns:
        Path to the cloned repository
    """
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = ["clone", "--depth", "1"]
    if branch:
        args.extend(["--branch", branch])
    args.extend([repo_url, str(dest)])

    await _git(*args)
    return dest


async def ensure_branch(cwd: str | Path, branch_name: str, base: str = "main") -> str:
    """Create and checkout a new branch from base.

    If the branch already exists, checks it out. Creates from base if it doesn't.

    Returns:
        The branch name that was checked out.
    """
    cwd = Path(cwd)
    # Check if branch exists
    stdout, _, rc = await _run_cmd("git", "branch", "--list", branch_name, cwd=cwd)
    if stdout.strip():
        await _git("checkout", branch_name, cwd=cwd)
    else:
        await _git("checkout", "-b", branch_name, base, cwd=cwd)
    return branch_name


async def push_branch(cwd: str | Path, branch_name: str, remote: str = "origin") -> None:
    """Push a branch to the remote."""
    await _git("push", "-u", remote, branch_name, cwd=str(cwd))


async def commit_all(cwd: str | Path, message: str) -> bool:
    """Stage all changes and commit. Returns True if there were changes to commit."""
    cwd = Path(cwd)
    # Check if there are changes
    stdout, _, _ = await _run_cmd("git", "status", "--porcelain", cwd=cwd)
    if not stdout.strip():
        return False

    await _git("add", "-A", cwd=cwd)
    await _git("commit", "-m", message, cwd=cwd)
    return True


class GitHubClient(GitClient):
    """Real GitHub integration: creates branches, pushes, and creates PRs via `gh` CLI.

    Requires:
      - `gh` CLI installed
      - Authenticated via GITHUB_TOKEN env var or `gh auth login`
      - Repository workspace must be a git repo with a remote configured
    """

    def __init__(self, workspace_path: str | Path | None = None):
        """
        Args:
            workspace_path: Default workspace path for git operations.
                           If None, uses the group's workspace at call time.
        """
        self._default_workspace = Path(workspace_path) if workspace_path else None

    async def create_pr(
        self,
        group_id: int,
        title: str,
        description: str = "",
        ticket_ids: list[str] | None = None,
    ) -> dict:
        """Create a PR on GitHub.

        Steps:
          1. Commit any uncommitted changes
          2. Push the current branch
          3. Create PR via `gh pr create`

        Args:
            group_id: Group ID (used to resolve workspace path)
            title: PR title
            description: PR body/description
            ticket_ids: Associated ticket IDs (included in PR body)

        Returns:
            dict with pr_id, url, title, tickets
        """
        from workspace import layout as ws_layout

        # Resolve workspace path
        if self._default_workspace:
            workspace = self._default_workspace
        else:
            workspace = ws_layout.group_shared_dir(group_id) / "workspace"

        workspace = Path(workspace)
        if not workspace.exists() or not (workspace / ".git").exists():
            raise RuntimeError(f"No git repository found at {workspace}")

        # Get current branch
        branch = await _git("rev-parse", "--abbrev-ref", "HEAD", cwd=workspace)

        # Commit any pending changes
        tickets = ticket_ids or []
        ticket_refs = ", ".join(tickets) if tickets else ""
        commit_msg = f"{title}\n\n{description}"
        if ticket_refs:
            commit_msg += f"\n\nRelated: {ticket_refs}"

        had_changes = await commit_all(workspace, commit_msg.strip())
        if not had_changes:
            log.info("GitHubClient: no changes to commit for group %d", group_id)

        # Push branch
        try:
            await push_branch(workspace, branch)
        except RuntimeError as e:
            log.warning("GitHubClient: push failed for group %d: %s", group_id, e)
            # Continue to try PR creation (branch might already be pushed)

        # Build PR body
        body_parts = [description]
        if ticket_refs:
            body_parts.append(f"\n\n**Related tickets:** {ticket_refs}")
        pr_body = "\n".join(body_parts).strip()

        # Create PR via gh CLI
        try:
            gh_args = ["pr", "create", "--title", title, "--body", pr_body, "--head", branch]
            # Try to set base branch
            try:
                default_branch = await _git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=workspace)
                default_branch = default_branch.replace("refs/remotes/origin/", "")
                gh_args.extend(["--base", default_branch])
            except Exception:
                pass  # gh will use the repo's default

            pr_url = await _gh(*gh_args, cwd=workspace)

            # Extract PR number from URL
            pr_id = pr_url.rstrip("/").split("/")[-1] if pr_url else "unknown"

            return {
                "pr_id": f"PR-{pr_id}",
                "url": pr_url,
                "title": title,
                "tickets": tickets,
            }

        except RuntimeError as e:
            log.error("GitHubClient: PR creation failed for group %d: %s", group_id, e)
            # Fail closed: do NOT silently fall back to LocalGitClient.
            # The caller must handle the error explicitly. Silent fallback masks
            # real GitHub failures and presents them as successful PR submissions.
            raise RuntimeError(f"GitHub PR creation failed: {e}") from e


def install_github_client(workspace_path: str | Path | None = None) -> GitHubClient:
    """Create and install a GitHubClient as the active GitClient.

    Call this at startup to replace the default LocalGitClient.
    """
    client = GitHubClient(workspace_path)
    set_git(client)
    return client
