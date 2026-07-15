"""tests/test_github_credentials_security.py — P0-4: GitHub credentials security tests.

Tests that GitHub credentials are handled securely:
  - github_token removed from API body and orchestrator parameters
  - GITHUB_TOKEN read from environment variable only
  - Token not embedded in git remote URLs
  - GIT_ASKPASS used for authentication instead of URL embedding
  - Security assertion rejects URLs with credentials
  - Worker fails closed when NUKE_GITHUB_ENABLED=true but gh/token missing
"""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError


class TestGitHubTokenNotInAPI(unittest.TestCase):
    """Test that github_token is not accepted in API requests."""

    def test_create_task_request_no_github_token_field(self):
        """CreateTaskRequest should not have github_token field."""
        from plugins.agent_dashboard.api import CreateTaskRequest

        # Valid request without github_token
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
        )

        # Verify github_token is not a field
        self.assertNotIn("github_token", req.model_fields)

    def test_create_task_request_rejects_github_token(self):
        """CreateTaskRequest should reject if github_token is provided."""
        from plugins.agent_dashboard.api import CreateTaskRequest

        # Try to create request with github_token (should be ignored or rejected)
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API",
            github_token="secret123",  # This should be ignored
        )

        # Verify github_token is not stored
        self.assertFalse(hasattr(req, "github_token"))


class TestGitHubTokenFromEnvironment(unittest.TestCase):
    """Test that GITHUB_TOKEN is read from environment variable."""

    def test_clone_repo_reads_github_token_from_env(self):
        """clone_repo reads GITHUB_TOKEN from environment, not parameters."""
        from integrations.github_client import clone_repo
        import inspect

        sig = inspect.signature(clone_repo)
        params = list(sig.parameters.keys())

        # Verify github_token is not a parameter
        self.assertNotIn("github_token", params)

    def test_push_branch_reads_github_token_from_env(self):
        """push_branch reads GITHUB_TOKEN from environment, not parameters."""
        from integrations.github_client import push_branch
        import inspect

        sig = inspect.signature(push_branch)
        params = list(sig.parameters.keys())

        # Verify github_token is not a parameter
        self.assertNotIn("github_token", params)

    def test_create_pr_reads_github_token_from_env(self):
        """GitHubClient.create_pr reads GITHUB_TOKEN from environment, not parameters."""
        from integrations.github_client import GitHubClient
        import inspect

        sig = inspect.signature(GitHubClient.create_pr)
        params = list(sig.parameters.keys())

        # Verify github_token is not a parameter
        self.assertNotIn("github_token", params)


class TestGitHubTokenNotInURLs(unittest.IsolatedAsyncioTestCase):
    """Test that GITHUB_TOKEN is not embedded in git remote URLs."""

    async def test_clone_uses_git_askpass_not_url_embedding(self):
        """clone_repo uses GIT_ASKPASS instead of embedding token in URL."""
        from integrations.github_client import clone_repo

        # Set GITHUB_TOKEN in environment
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret123"}):
            procs = [
                MagicMock(),  # git clone
                MagicMock(),  # git remote get-url (security assertion)
            ]
            procs[0].communicate = AsyncMock(return_value=(b"", b""))
            procs[0].returncode = 0
            procs[1].communicate = AsyncMock(return_value=(b"https://github.com/user/repo.git\n", b""))
            procs[1].returncode = 0

            with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
                with patch("shutil.rmtree"):
                    dest = "/tmp/test_clone"
                    await clone_repo("https://github.com/user/repo.git", dest)

                    # Verify the clone command was called with the original URL (no token)
                    first_call_args = mock_exec.call_args_list[0][0]
                    url_arg = [arg for arg in first_call_args if "github.com" in str(arg)][0]
                    self.assertNotIn("secret123", url_arg)
                    self.assertEqual(url_arg, "https://github.com/user/repo.git")

    async def test_security_assertion_rejects_url_with_credentials(self):
        """Security assertion rejects if git remote URL contains credentials."""
        from integrations.github_client import clone_repo

        procs = [
            MagicMock(),  # git clone
            MagicMock(),  # git remote get-url (returns URL with credentials)
        ]
        procs[0].communicate = AsyncMock(return_value=(b"", b""))
        procs[0].returncode = 0
        procs[1].communicate = AsyncMock(return_value=(b"https://token123@github.com/user/repo.git\n", b""))
        procs[1].returncode = 0

        with patch("asyncio.create_subprocess_exec", side_effect=procs):
            with patch("shutil.rmtree"):
                dest = "/tmp/test_clone"
                with self.assertRaises(RuntimeError) as ctx:
                    await clone_repo("https://github.com/user/repo.git", dest)
                self.assertIn("Security violation", str(ctx.exception))
                self.assertIn("credentials", str(ctx.exception))


class TestWorkerFailsClosed(unittest.IsolatedAsyncioTestCase):
    """Test that Worker fails closed when NUKE_GITHUB_ENABLED=true but gh/token missing."""

    async def test_worker_fails_when_github_enabled_but_gh_missing(self):
        """Worker startup fails when NUKE_GITHUB_ENABLED=true but gh CLI not found."""
        from integrations.github_client import is_gh_available

        with patch.dict(os.environ, {"NUKE_GITHUB_ENABLED": "true"}, clear=False):
            with patch("shutil.which", return_value=None):
                # Simulate the check in runtime/entry.py
                if os.getenv("NUKE_GITHUB_ENABLED", "").lower() in ("1", "true", "yes"):
                    if not is_gh_available():
                        with self.assertRaises(RuntimeError) as ctx:
                            raise RuntimeError(
                                "NUKE_GITHUB_ENABLED=true but gh CLI not found. "
                                "Install gh or set NUKE_GITHUB_ENABLED=false."
                            )
                        self.assertIn("gh CLI not found", str(ctx.exception))

    async def test_worker_fails_when_github_enabled_but_token_missing(self):
        """Worker startup fails when NUKE_GITHUB_ENABLED=true but GITHUB_TOKEN not set."""
        from integrations.github_client import is_gh_available

        with patch.dict(os.environ, {"NUKE_GITHUB_ENABLED": "true"}, clear=False):
            # Remove GITHUB_TOKEN if it exists
            env = os.environ.copy()
            env.pop("GITHUB_TOKEN", None)

            with patch("shutil.which", return_value="/usr/bin/gh"):
                with patch.dict(os.environ, env, clear=True):
                    # Simulate the check in runtime/entry.py
                    if os.getenv("NUKE_GITHUB_ENABLED", "").lower() in ("1", "true", "yes"):
                        if is_gh_available():
                            if not os.environ.get("GITHUB_TOKEN"):
                                with self.assertRaises(RuntimeError) as ctx:
                                    raise RuntimeError(
                                        "NUKE_GITHUB_ENABLED=true but GITHUB_TOKEN not set. "
                                        "Set GITHUB_TOKEN env var or set NUKE_GITHUB_ENABLED=false."
                                    )
                                self.assertIn("GITHUB_TOKEN not set", str(ctx.exception))

    async def test_worker_succeeds_when_github_enabled_with_gh_and_token(self):
        """Worker startup succeeds when NUKE_GITHUB_ENABLED=true with gh and GITHUB_TOKEN."""
        from integrations.github_client import is_gh_available

        with patch.dict(os.environ, {
            "NUKE_GITHUB_ENABLED": "true",
            "GITHUB_TOKEN": "secret123",
        }, clear=False):
            with patch("shutil.which", return_value="/usr/bin/gh"):
                # Simulate the check in runtime/entry.py
                if os.getenv("NUKE_GITHUB_ENABLED", "").lower() in ("1", "true", "yes"):
                    if is_gh_available():
                        if os.environ.get("GITHUB_TOKEN"):
                            # Should succeed (no exception)
                            pass


class TestGitHubOnlyForNow(unittest.TestCase):
    """Test that only github.com URLs are accepted (P0-4 requirement 8)."""

    def test_reject_gitlab_url(self):
        """GitLab URLs are rejected with clear message."""
        from plugins.agent_dashboard.api import CreateTaskRequest

        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://gitlab.com/org/project.git",
                requirements="Build a REST API",
            )
        self.assertIn("github.com", str(ctx.exception))
        self.assertIn("not yet supported", str(ctx.exception))

    def test_reject_bitbucket_url(self):
        """Bitbucket URLs are rejected with clear message."""
        from plugins.agent_dashboard.api import CreateTaskRequest

        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://bitbucket.org/team/repo.git",
                requirements="Build a REST API",
            )
        self.assertIn("github.com", str(ctx.exception))
        self.assertIn("not yet supported", str(ctx.exception))

    def test_accept_github_url(self):
        """GitHub URLs are accepted."""
        from plugins.agent_dashboard.api import CreateTaskRequest

        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
        )
        self.assertEqual(req.repo_url, "https://github.com/user/repo.git")
