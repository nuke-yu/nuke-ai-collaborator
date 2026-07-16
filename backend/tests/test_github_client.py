"""tests/test_github_client.py — GitHubClient unit tests.

Tests the GitHub integration logic using mocked subprocess calls.
No real git/gh commands are executed.
"""
import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from integrations.github_client import (
    GitHubClient,
    _run_cmd,
    _git,
    _gh,
    clone_repo,
    ensure_branch,
    commit_all,
    is_gh_available,
    push_branch,
)


def _mock_proc(stdout=b"", stderr=b"", rc=0):
    """Create a mock subprocess with given stdout/stderr/returncode."""
    p = AsyncMock()
    p.communicate = AsyncMock(return_value=(stdout, stderr))
    p.returncode = rc
    p.kill = MagicMock()
    return p


class TestRunCmd(unittest.IsolatedAsyncioTestCase):

    async def test_success(self):
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(b"hello\n", b"", 0)):
            stdout, stderr, rc = await _run_cmd("echo", "hello")
            self.assertEqual(stdout, "hello")
            self.assertEqual(rc, 0)

    async def test_failure(self):
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(b"", b"error msg", 1)):
            stdout, stderr, rc = await _run_cmd("false")
            self.assertEqual(rc, 1)
            self.assertEqual(stderr, "error msg")

    async def test_parent_credentials_are_not_inherited(self):
        proc = _mock_proc()
        with patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "github-secret",
                "GH_TOKEN": "gh-secret",
                "AWS_SECRET_ACCESS_KEY": "aws-secret",
                "PATH": "/usr/bin",
            },
            clear=True,
        ):
            with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
                await _run_cmd("git", "status")

        child_env = mock_exec.call_args.kwargs["env"]
        self.assertEqual(child_env["PATH"], "/usr/bin")
        self.assertEqual(child_env["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn("GITHUB_TOKEN", child_env)
        self.assertNotIn("GH_TOKEN", child_env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", child_env)

    async def test_timeout(self):
        proc = _mock_proc()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with self.assertRaises(RuntimeError) as ctx:
                await _run_cmd("sleep", "100", timeout=1)
            self.assertIn("timed out", str(ctx.exception))
            proc.kill.assert_called_once()
            proc.wait.assert_awaited_once()


class TestGitHelper(unittest.IsolatedAsyncioTestCase):

    async def test_success(self):
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(b"main\n", b"", 0)):
            result = await _git("rev-parse", "--abbrev-ref", "HEAD")
            self.assertEqual(result, "main")

    async def test_failure_raises(self):
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(b"", b"fatal: not a git repo", 128)):
            with self.assertRaises(RuntimeError) as ctx:
                await _git("status")
            self.assertIn("not a git repo", str(ctx.exception))

    async def test_authenticated_git_receives_only_explicit_github_token(self):
        with patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "github-secret",
                "UNRELATED_SECRET": "do-not-inherit",
            },
            clear=True,
        ):
            with patch(
                "asyncio.create_subprocess_exec",
                return_value=_mock_proc(),
            ) as mock_exec:
                await _git("push", authenticated=True)

        child_env = mock_exec.call_args.kwargs["env"]
        self.assertEqual(child_env["GITHUB_TOKEN"], "github-secret")
        self.assertIn("GIT_ASKPASS", child_env)
        self.assertEqual(child_env["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(child_env["GIT_CONFIG_KEY_0"], "credential.helper")
        self.assertEqual(child_env["GIT_CONFIG_VALUE_0"], "")
        self.assertEqual(child_env["GIT_CONFIG_KEY_1"], "credential.helper")
        self.assertTrue(child_env["GIT_CONFIG_VALUE_1"].endswith(
            "git_credential_github.sh"
        ))
        self.assertNotIn("GH_TOKEN", child_env)
        self.assertNotIn("UNRELATED_SECRET", child_env)


class TestGhHelper(unittest.IsolatedAsyncioTestCase):

    async def test_success(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "github-secret"}, clear=True):
            with patch("asyncio.create_subprocess_exec",
                        return_value=_mock_proc(b"https://github.com/user/repo/pull/42\n", b"", 0)) as mock_exec:
                result = await _gh("pr", "create", "--title", "test")
                self.assertIn("pull/42", result)

        child_env = mock_exec.call_args.kwargs["env"]
        self.assertEqual(child_env["GH_TOKEN"], "github-secret")
        self.assertNotIn("GITHUB_TOKEN", child_env)

    async def test_failure_raises(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "github-secret"}, clear=True):
            with patch("asyncio.create_subprocess_exec",
                        return_value=_mock_proc(b"", b"not authenticated", 1)):
                with self.assertRaises(RuntimeError) as ctx:
                    await _gh("pr", "create")
                self.assertIn("not authenticated", str(ctx.exception))

    async def test_missing_token_fails_before_spawning(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                with self.assertRaisesRegex(RuntimeError, "GITHUB_TOKEN"):
                    await _gh("pr", "create")
        mock_exec.assert_not_called()


class TestCloneRepo(unittest.IsolatedAsyncioTestCase):

    async def test_clone_with_branch(self):
        # P0-4: clone_repo now makes two git calls: clone + security assertion
        procs = [
            _mock_proc(b"", b"", 0),   # git clone
            _mock_proc(b"https://github.com/user/repo.git\n", b"", 0),  # git remote get-url
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
            with patch("shutil.rmtree"):
                dest = Path("/tmp/test_clone_dest")
                await clone_repo("https://github.com/user/repo.git", dest, branch="develop")
                # First call should be clone
                first_call_args = mock_exec.call_args_list[0][0]
                self.assertIn("clone", first_call_args)
                self.assertIn("--branch", first_call_args)
                self.assertIn("develop", first_call_args)

    async def test_clone_without_branch(self):
        # P0-4: clone_repo now makes two git calls: clone + security assertion
        procs = [
            _mock_proc(b"", b"", 0),   # git clone
            _mock_proc(b"https://github.com/user/repo.git\n", b"", 0),  # git remote get-url
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
            with patch("shutil.rmtree"):
                dest = Path("/tmp/test_clone_dest")
                await clone_repo("https://github.com/user/repo.git", dest)
                # First call should be clone
                first_call_args = mock_exec.call_args_list[0][0]
                self.assertNotIn("--branch", first_call_args)

    async def test_clone_security_assertion_passes(self):
        """P0-4: Security assertion passes when remote URL has no credentials."""
        procs = [
            _mock_proc(b"", b"", 0),   # git clone
            _mock_proc(b"https://github.com/user/repo.git\n", b"", 0),  # git remote get-url
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs):
            with patch("shutil.rmtree"):
                dest = Path("/tmp/test_clone_dest")
                # Should not raise
                await clone_repo("https://github.com/user/repo.git", dest)

    async def test_clone_security_assertion_fails_with_credentials(self):
        """P0-4: Security assertion fails when remote URL contains credentials."""
        procs = [
            _mock_proc(b"", b"", 0),   # git clone
            _mock_proc(b"https://token123@github.com/user/repo.git\n", b"", 0),  # git remote get-url
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs):
            with patch("shutil.rmtree"):
                dest = Path("/tmp/test_clone_dest")
                with self.assertRaises(RuntimeError) as ctx:
                    await clone_repo("https://github.com/user/repo.git", dest)
                self.assertIn("Security violation", str(ctx.exception))
                self.assertIn("credentials", str(ctx.exception))

    async def test_clone_rejects_untrusted_host_before_spawning(self):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            with self.assertRaisesRegex(ValueError, "not yet supported"):
                await clone_repo(
                    "https://gitlab.com/user/repo.git", "/tmp/test_clone_dest"
                )
        mock_exec.assert_not_called()


class TestPushBranch(unittest.IsolatedAsyncioTestCase):

    async def test_push_pins_validated_remote_url(self):
        procs = [
            _mock_proc(b"https://github.com/user/repo.git\n", b"", 0),
            _mock_proc(),
        ]
        with patch.dict(os.environ, {"GITHUB_TOKEN": "github-secret"}, clear=True):
            with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
                remote_url = await push_branch("/tmp/repo", "feature/test")

        self.assertEqual(remote_url, "https://github.com/user/repo.git")
        push_args = mock_exec.call_args_list[1].args
        self.assertIn(
            "remote.origin.url=https://github.com/user/repo.git", push_args
        )

    async def test_push_rejects_untrusted_remote_before_auth_command(self):
        proc = _mock_proc(b"https://evil.example/user/repo.git\n", b"", 0)
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            with self.assertRaisesRegex(RuntimeError, "untrusted remote"):
                await push_branch("/tmp/repo", "feature/test")

        self.assertEqual(mock_exec.call_count, 1)


class TestEnsureBranch(unittest.IsolatedAsyncioTestCase):

    async def test_new_branch(self):
        procs = [
            _mock_proc(b"", b"", 0),   # branch --list → empty
            _mock_proc(b"", b"", 0),   # checkout -b → success
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs):
            branch = await ensure_branch("/tmp/repo", "feature/test", "main")
            self.assertEqual(branch, "feature/test")

    async def test_existing_branch(self):
        procs = [
            _mock_proc(b"  feature/test\n", b"", 0),  # branch --list → found
            _mock_proc(b"", b"", 0),                   # checkout → success
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs):
            branch = await ensure_branch("/tmp/repo", "feature/test")
            self.assertEqual(branch, "feature/test")


class TestCommitAll(unittest.IsolatedAsyncioTestCase):

    async def test_with_changes(self):
        procs = [
            _mock_proc(b" M file.py\n", b"", 0),  # status → has changes
            _mock_proc(b"", b"", 0),               # add
            _mock_proc(b"", b"", 0),               # commit
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs):
            result = await commit_all("/tmp/repo", "test commit")
            self.assertTrue(result)

    async def test_no_changes(self):
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(b"", b"", 0)):
            result = await commit_all("/tmp/repo", "test commit")
            self.assertFalse(result)


class TestGitHubClient(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._env = patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _setup_workspace(self, tmp_dir):
        """Create a fake workspace with .git directory."""
        workspace = Path(tmp_dir) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".git").mkdir()
        return workspace

    async def test_create_pr_success(self):
        """Full PR creation flow succeeds."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = self._setup_workspace(tmp_dir)
            client = GitHubClient(workspace_path=workspace)

            mock_results = [
                (b"feature/task-1\n", b"", 0),   # rev-parse branch
                (b" M main.py\n", b"", 0),        # status → changes
                (b"", b"", 0),                     # add
                (b"", b"", 0),                     # commit
                (b"https://github.com/user/repo.git\n", b"", 0),  # remote URL
                (b"", b"", 0),                     # push
                (b"refs/remotes/origin/main\n", b"", 0),  # symbolic-ref
                (b"https://github.com/user/repo/pull/42\n", b"", 0),  # gh pr create
            ]
            procs = [_mock_proc(*r) for r in mock_results]

            with patch("asyncio.create_subprocess_exec", side_effect=procs):
                result = await client.create_pr(
                    group_id=1,
                    title="Add feature X",
                    description="Implements feature X",
                    ticket_ids=["DFT-1", "DFT-2"],
                )

            self.assertEqual(result["pr_id"], "PR-42")
            self.assertIn("pull/42", result["url"])
            self.assertEqual(result["title"], "Add feature X")
            self.assertEqual(result["tickets"], ["DFT-1", "DFT-2"])

    async def test_create_pr_no_changes(self):
        """PR creation with no uncommitted changes still works."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = self._setup_workspace(tmp_dir)
            client = GitHubClient(workspace_path=workspace)

            mock_results = [
                (b"main\n", b"", 0),          # rev-parse branch
                (b"", b"", 0),                 # status → no changes
                (b"https://github.com/user/repo.git\n", b"", 0),  # remote URL
                (b"", b"", 0),                 # push
                (b"refs/remotes/origin/main\n", b"", 0),  # symbolic-ref
                (b"https://github.com/user/repo/pull/7\n", b"", 0),  # gh pr create
            ]
            procs = [_mock_proc(*r) for r in mock_results]

            with patch("asyncio.create_subprocess_exec", side_effect=procs):
                result = await client.create_pr(
                    group_id=1,
                    title="Minor fix",
                    description="",
                )

            self.assertIn("pull/7", result["url"])

    async def test_create_pr_no_git_repo(self):
        """Raises when workspace has no .git directory."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / "empty"
            workspace.mkdir()
            client = GitHubClient(workspace_path=workspace)
            with self.assertRaises(RuntimeError) as ctx:
                await client.create_pr(group_id=1, title="test")
            self.assertIn("No git repository", str(ctx.exception))

    async def test_create_pr_gh_failure_raises(self):
        """When gh pr create fails, raises RuntimeError (fail closed, no silent fallback)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = self._setup_workspace(tmp_dir)
            client = GitHubClient(workspace_path=workspace)

            mock_results = [
                (b"feature/x\n", b"", 0),          # rev-parse branch
                (b" M file.py\n", b"", 0),          # status → changes
                (b"", b"", 0),                       # add
                (b"", b"", 0),                       # commit
                (b"https://github.com/user/repo.git\n", b"", 0),  # remote URL
                (b"", b"", 0),                       # push
                (b"refs/remotes/origin/main\n", b"", 0),  # symbolic-ref
                (b"", b"GraphQL: Could not resolve to a Repository\n", 1),  # gh FAILS
            ]
            procs = [_mock_proc(*r) for r in mock_results]

            with patch("asyncio.create_subprocess_exec", side_effect=procs):
                with self.assertRaises(RuntimeError) as ctx:
                    await client.create_pr(
                        group_id=1,
                        title="test",
                        description="",
                    )
            self.assertIn("PR creation failed", str(ctx.exception))


class TestIsGhAvailable(unittest.TestCase):

    def test_available(self):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            self.assertTrue(is_gh_available())

    def test_not_available(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(is_gh_available())
