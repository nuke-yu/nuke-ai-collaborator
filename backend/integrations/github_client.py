"""integrations/github_client.py — Real GitHub integration via `gh` CLI.

Implements GitClient ABC from integrations/git.py using the GitHub CLI (`gh`)
for branch management, push, and PR creation. Missing capabilities fail closed.

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
from urllib.parse import urlsplit

from integrations.git import GitClient, set_git
from integrations.repository_policy import DEFAULT_REPOSITORY_ADMISSION_POLICY

log = logging.getLogger(__name__)

# Default timeout for git/gh commands
_CMD_TIMEOUT = 60
_TRUE_VALUES = {"1", "true", "yes"}
_CREDENTIAL_HELPER_PATH = Path(__file__).with_name("git_credential_github.sh")
_FALSE_PATH = shutil.which("false") or "/bin/false"
_SUBPROCESS_ENV_KEYS = {
    "ALL_PROXY",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_NAME",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
}


class GitHubIntegrationUnavailable(RuntimeError):
    """The deployment cannot provide real GitHub repository/PR operations."""


def github_integration_enabled() -> bool:
    return os.getenv("NUKE_GITHUB_ENABLED", "").lower() in _TRUE_VALUES


def require_github_integration() -> None:
    """Validate the complete capability needed by coding-agent tasks."""
    if not github_integration_enabled():
        raise GitHubIntegrationUnavailable(
            "GitHub integration is disabled; set NUKE_GITHUB_ENABLED=true"
        )
    if not os.environ.get("GITHUB_TOKEN"):
        raise GitHubIntegrationUnavailable(
            "GitHub integration is unavailable: GITHUB_TOKEN is not set"
        )
    if not is_gh_available():
        raise GitHubIntegrationUnavailable(
            "GitHub integration is unavailable: gh CLI is not installed"
        )
    if not _CREDENTIAL_HELPER_PATH.is_file() or not os.access(
        _CREDENTIAL_HELPER_PATH, os.X_OK
    ):
        raise GitHubIntegrationUnavailable(
            f"GitHub credential helper is unavailable: {_CREDENTIAL_HELPER_PATH}"
        )


def _github_auth_env(*, require_token: bool = False) -> dict[str, str]:
    """Return non-interactive GitHub auth settings without writing credentials."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if require_token and not token:
        raise GitHubIntegrationUnavailable("GITHUB_TOKEN is required")

    env = {"GIT_TERMINAL_PROMPT": "0"}
    if token:
        env.update({
            "GITHUB_TOKEN": token,
            "GIT_ASKPASS": _FALSE_PATH,
            "GIT_CONFIG_COUNT": "2",
            # Empty credential.helper resets helpers inherited from system,
            # global, and repository config before installing the scoped helper.
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": str(_CREDENTIAL_HELPER_PATH),
        })
    return env


def _github_cli_auth_env() -> dict[str, str]:
    """Return the one credential variable exposed to an authenticated gh call."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise GitHubIntegrationUnavailable("GITHUB_TOKEN is required")
    return {"GH_TOKEN": token}


def _subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build a minimal child environment; credentials require explicit injection."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _SUBPROCESS_ENV_KEYS
    }
    env.setdefault("PATH", os.defpath)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra_env:
        env.update(extra_env)
    return env


async def _run_cmd(*args: str, cwd: str | Path | None = None, timeout: int = _CMD_TIMEOUT,
                   extra_env: dict | None = None) -> tuple[str, str, int]:
    """Run a command asynchronously. Returns (stdout, stderr, returncode)."""
    env = _subprocess_env(extra_env)
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            log.exception("Failed to kill timed-out command: %s", args[0])
        try:
            await proc.wait()
        except Exception:
            log.exception("Failed to reap timed-out command: %s", args[0])
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(args)}")

    return (
        stdout.decode(errors="replace").strip(),
        stderr.decode(errors="replace").strip(),
        proc.returncode or 0,
    )


async def _git(
    *args: str,
    cwd: str | Path | None = None,
    authenticated: bool = False,
) -> str:
    """Run a git command, raising on failure."""
    auth_env = _github_auth_env(require_token=True) if authenticated else None
    stdout, stderr, rc = await _run_cmd(
        "git", *args, cwd=cwd, extra_env=auth_env
    )
    if rc != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc={rc}): {stderr}")
    return stdout


async def _gh(*args: str, cwd: str | Path | None = None) -> str:
    """Run an authenticated gh CLI command, raising on failure."""
    stdout, stderr, rc = await _run_cmd(
        "gh", *args, cwd=cwd, extra_env=_github_cli_auth_env()
    )
    if rc != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed (rc={rc}): {stderr}")
    return stdout


def is_gh_available() -> bool:
    """Check if `gh` CLI is installed and authenticated."""
    return shutil.which("gh") is not None


async def clone_repo(repo_url: str, dest: str | Path, branch: str = "") -> Path:
    """Clone a git repository to the destination directory.

    P0-4: GitHub credentials are read from GITHUB_TOKEN environment variable,
    not passed as parameters. Uses a host-scoped Git credential helper instead
    of embedding the token in the URL or parsing askpass prompts.

    Args:
        repo_url: Git repository URL (HTTPS)
        dest: Destination directory path
        branch: Optional branch to checkout after clone

    Returns:
        Path to the cloned repository

    Raises:
        RuntimeError: if clone fails or if remote URL contains credentials (security check)
    """
    DEFAULT_REPOSITORY_ADMISSION_POLICY.validate(repo_url)
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = ["clone", "--depth", "1"]
    if branch:
        args.extend(["--branch", branch])
    args.extend([repo_url, str(dest)])

    _, stderr, rc = await _run_cmd(
        "git", *args, cwd=None, extra_env=_github_auth_env()
    )
    if rc != 0:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"git clone failed (rc={rc}): {stderr}")

    # P0-4 Security assertion: verify remote URL doesn't contain credentials
    remote_url = await _git("remote", "get-url", "origin", cwd=dest)
    try:
        DEFAULT_REPOSITORY_ADMISSION_POLICY.validate(remote_url)
    except ValueError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(
            f"Security violation: cloned repository has an invalid origin URL: {exc}"
        ) from exc

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


async def push_branch(
    cwd: str | Path, branch_name: str, remote: str = "origin"
) -> str:
    """Push a branch to the remote.

    P0-4: GITHUB_TOKEN is injected only into the validated push subprocess.
    """
    cwd = Path(cwd)
    valid_remote_chars = (
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    )
    if not remote or any(char not in valid_remote_chars for char in remote):
        raise ValueError(f"Invalid git remote name: {remote}")
    remote_url = await _git("remote", "get-url", remote, cwd=cwd)
    try:
        DEFAULT_REPOSITORY_ADMISSION_POLICY.validate(remote_url)
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing authenticated push to untrusted remote {remote}: {exc}"
        ) from exc

    # Pin this invocation to the validated URL so a concurrent config rewrite
    # cannot redirect the credential-bearing push to another host.
    await _git(
        "-c",
        f"remote.{remote}.url={remote_url}",
        "push",
        "-u",
        remote,
        branch_name,
        cwd=str(cwd),
        authenticated=True,
    )
    return remote_url


async def ls_remote(repo_url: str, branch: str = "", timeout: int = 30) -> None:
    """Verify repository/branch reachability using the shared auth contract."""
    DEFAULT_REPOSITORY_ADMISSION_POLICY.validate(repo_url)
    args = ["ls-remote", "--exit-code", repo_url]
    if branch:
        args.extend(["--heads", branch])
    _, stderr, rc = await _run_cmd(
        "git",
        *args,
        timeout=timeout,
        extra_env=_github_auth_env(require_token=True),
    )
    if rc != 0:
        raise RuntimeError(
            f"Repository not reachable or branch '{branch}' not found: {stderr}"
        )


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

        P0-4: GITHUB_TOKEN is read from environment variable, not passed as parameter.
        The gh CLI automatically uses GITHUB_TOKEN env var for authentication.

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

        # Push branch (fail closed — don't create PR against stale remote branch)
        remote_url = await push_branch(workspace, branch)

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

            repository = (
                urlsplit(remote_url).path.removeprefix("/").removesuffix(".git")
            )
            pr_url = await _gh(*gh_args, "--repo", repository, cwd=workspace)

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
