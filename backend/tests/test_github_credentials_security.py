"""tests/test_github_credentials_security.py — P0-4: GitHub credentials security tests.

Tests that GitHub credentials are handled securely:
  - github_token removed from API body and orchestrator parameters
  - GITHUB_TOKEN read from environment variable only
  - Token not embedded in git remote URLs
  - static GIT_ASKPASS helper reads credentials only from process environment
  - Security assertion rejects URLs with credentials
  - Worker fails closed when NUKE_GITHUB_ENABLED=true but gh/token missing
"""
import os
import subprocess
import unittest
from pathlib import Path
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

                    child_env = mock_exec.call_args_list[0].kwargs["env"]
                    askpass = child_env["GIT_ASKPASS"]
                    self.assertNotIn("secret123", askpass)
                    self.assertNotIn("secret123", Path(askpass).read_text(encoding="utf-8"))
                    self.assertFalse(os.path.exists("/tmp/.git_askpass.sh"))

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

    async def test_askpass_rejects_non_github_prompt(self):
        from integrations import github_client

        result = subprocess.run(
            [
                str(github_client._ASKPASS_PATH),
                "Password for 'https://x-access-token@evil.example': ",
            ],
            capture_output=True,
            check=False,
            env={"GITHUB_TOKEN": "secret123", "PATH": os.defpath},
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    async def test_askpass_releases_token_only_for_github_prompt(self):
        from integrations import github_client

        result = subprocess.run(
            [
                str(github_client._ASKPASS_PATH),
                "Password for 'https://x-access-token@github.com': ",
            ],
            capture_output=True,
            check=True,
            env={"GITHUB_TOKEN": "secret123", "PATH": os.defpath},
            text=True,
        )

        self.assertEqual(result.stdout, "secret123\n")


class TestWorkerFailsClosed(unittest.IsolatedAsyncioTestCase):
    """Test that Worker fails closed when NUKE_GITHUB_ENABLED=true but gh/token missing."""

    async def test_worker_fails_when_github_enabled_but_gh_missing(self):
        from integrations.github_client import require_github_integration

        with patch.dict(
            os.environ,
            {"NUKE_GITHUB_ENABLED": "true", "GITHUB_TOKEN": "secret123"},
            clear=False,
        ):
            with patch("shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "gh CLI"):
                    require_github_integration()

    async def test_worker_fails_when_github_enabled_but_token_missing(self):
        """Worker startup fails when NUKE_GITHUB_ENABLED=true but GITHUB_TOKEN not set."""
        from integrations.github_client import require_github_integration

        with patch.dict(os.environ, {"NUKE_GITHUB_ENABLED": "true"}, clear=True), \
             patch("shutil.which", return_value="/usr/bin/gh"):
            with self.assertRaisesRegex(RuntimeError, "GITHUB_TOKEN"):
                require_github_integration()

    async def test_worker_succeeds_when_github_enabled_with_gh_and_token(self):
        """Worker startup succeeds when NUKE_GITHUB_ENABLED=true with gh and GITHUB_TOKEN."""
        from integrations.github_client import require_github_integration

        with patch.dict(os.environ, {
            "NUKE_GITHUB_ENABLED": "true",
            "GITHUB_TOKEN": "secret123",
        }, clear=False):
            with patch("shutil.which", return_value="/usr/bin/gh"):
                require_github_integration()

    async def test_disabled_integration_fails_closed(self):
        from integrations.github_client import require_github_integration

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                require_github_integration()

    async def test_task_api_returns_503_when_integration_is_unavailable(self):
        from fastapi import HTTPException
        from integrations.github_client import GitHubIntegrationUnavailable
        from plugins.agent_dashboard import api

        orchestrator = MagicMock()
        orchestrator.create_task = AsyncMock(
            side_effect=GitHubIntegrationUnavailable("GitHub integration is disabled")
        )
        previous = api._orchestrator
        api._orchestrator = orchestrator
        try:
            request = api.CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Implement a sufficiently detailed feature",
            )
            with self.assertRaises(HTTPException) as ctx:
                await api.create_task(
                    request,
                    idempotency_key="request-123",
                    user={"username": "operator"},
                )
        finally:
            api._orchestrator = previous

        self.assertEqual(ctx.exception.status_code, 503)

    async def test_production_default_never_creates_local_stub_pr(self):
        from integrations.git import UnavailableGitClient, _default_client

        with patch.dict(os.environ, {"NUKE_ENV": "production"}, clear=False):
            self.assertIsInstance(_default_client(), UnavailableGitClient)


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
