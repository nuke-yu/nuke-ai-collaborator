"""Tests for P1 safety features: sensitive path protection and doom loop guard."""
import sys
import os
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Sensitive path protection
# ---------------------------------------------------------------------------

class TestSensitivePathDetection(unittest.TestCase):

    def _is_sensitive(self, path: str) -> bool:
        from executors.plugins.workspace_tools import _is_sensitive_path
        return _is_sensitive_path(path)

    def test_ssh_dir_blocked(self):
        self.assertTrue(self._is_sensitive("~/.ssh/id_rsa"))

    def test_ssh_known_hosts_blocked(self):
        self.assertTrue(self._is_sensitive("~/.ssh/known_hosts"))

    def test_aws_credentials_blocked(self):
        self.assertTrue(self._is_sensitive("~/.aws/credentials"))

    def test_aws_config_blocked(self):
        self.assertTrue(self._is_sensitive("~/.aws/config"))

    def test_dotenv_file_blocked(self):
        self.assertTrue(self._is_sensitive("/project/.env"))

    def test_dotenv_local_blocked(self):
        self.assertTrue(self._is_sensitive("/project/.env.local"))

    def test_pem_key_blocked(self):
        self.assertTrue(self._is_sensitive("/certs/server.pem"))

    def test_private_key_blocked(self):
        self.assertTrue(self._is_sensitive("/certs/server.key"))

    def test_normal_file_allowed(self):
        self.assertFalse(self._is_sensitive("/home/user/project/main.py"))

    def test_normal_env_dir_allowed(self):
        # a directory named "env" (not .env file) should be allowed
        self.assertFalse(self._is_sensitive("/home/user/project/env/activate"))

    def test_dotenv_example_allowed(self):
        # .env.example is commonly committed and not sensitive
        self.assertFalse(self._is_sensitive("/project/.env.example"))

    def test_awslike_dir_not_blocked(self):
        # .awslike is NOT .aws — should not be blocked by prefix match
        self.assertFalse(self._is_sensitive("/home/user/.awslike/config"))

    def test_uppercase_pem_blocked(self):
        # case-insensitive on macOS
        self.assertTrue(self._is_sensitive("/certs/server.PEM"))

    def test_dotenv_with_dotdot_traversal(self):
        """resolve() normalizes '..' traversal before checking filename."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            traversal = os.path.join(tmpdir, "subdir", "..", ".env")
            self.assertTrue(self._is_sensitive(traversal))


class TestSensitivePathHandlers(unittest.IsolatedAsyncioTestCase):

    async def test_read_local_file_blocked_for_ssh(self):
        from executors.plugins.workspace_tools import _handle_read_local_file
        result = await _handle_read_local_file("~/.ssh/id_rsa")
        self.assertIn("安全拒绝", result)
        self.assertIn("~/.ssh/id_rsa", result)

    async def test_write_local_file_blocked_for_dotenv(self):
        from executors.plugins.workspace_tools import _handle_write_local_file
        result = await _handle_write_local_file("/project/.env", "SECRET=abc")
        self.assertIn("安全拒绝", result)

    async def test_read_local_file_allowed_for_normal_path(self):
        from executors.plugins.workspace_tools import _handle_read_local_file
        # should reach the real read attempt and fail with FileNotFoundError, not a security block
        result = await _handle_read_local_file("/nonexistent/normal/file.txt")
        self.assertNotIn("安全拒绝", result)
        self.assertIn("文件不存在", result)


# ---------------------------------------------------------------------------
# Doom loop protection
# ---------------------------------------------------------------------------

class TestDoomLoopProtection(unittest.IsolatedAsyncioTestCase):
    """Verify the tool loop breaks after _DOOM_LOOP_THRESHOLD consecutive tool-only iterations."""

    async def _run_loop(self, responses: list) -> str:
        """Helper: run _tool_loop_core with a fake AI that returns canned responses."""
        from executors.plugins.tool_loop_v1 import _tool_loop_core

        call_count = 0

        async def fake_call_ai_once(system_prompt, messages, provider, model,
                                    temperature, max_tokens, tools=None, **kwargs):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            with patch("executors.plugins.tool_loop_v1._execute_tool_call",
                       new=AsyncMock(return_value="tool output")):
                result = await _tool_loop_core(
                    system_prompt="sys",
                    messages=[{"role": "user", "content": "hi"}],
                    provider="claude",
                    model_name="claude-opus-4-7",
                    temperature=0.7,
                    max_tokens=4096,
                    tool_schemas=[{"name": "run_shell"}],
                    max_iter=50,
                )
        return result

    async def test_doom_loop_triggers_after_threshold(self):
        """After THRESHOLD consecutive tool-call-only responses, loop breaks with protection message."""
        from executors.plugins.tool_loop_v1 import _DOOM_LOOP_THRESHOLD
        tool_only = {
            "type": "tool_calls",
            "calls": [{"name": "run_shell", "arguments": {"command": "echo hi"}, "id": "c1"}],
            "assistant_message": {"role": "assistant", "content": ""},
        }
        result = await self._run_loop([tool_only] * (_DOOM_LOOP_THRESHOLD + 2))
        self.assertIn("循环保护", result)

    async def test_doom_loop_does_not_trigger_below_threshold(self):
        """THRESHOLD-1 tool calls followed by a text response — no protection message."""
        from executors.plugins.tool_loop_v1 import _DOOM_LOOP_THRESHOLD
        tool_only = {
            "type": "tool_calls",
            "calls": [{"name": "run_shell", "arguments": {"command": "echo hi"}, "id": "c1"}],
            "assistant_message": {"role": "assistant", "content": ""},
        }
        text = {"type": "text", "content": "done"}
        responses = [tool_only] * (_DOOM_LOOP_THRESHOLD - 1) + [text]
        result = await self._run_loop(responses)
        self.assertEqual(result, "done")

    async def test_doom_loop_triggers_at_exact_threshold(self):
        """Exactly THRESHOLD consecutive tool-only responses triggers the protection."""
        from executors.plugins.tool_loop_v1 import _DOOM_LOOP_THRESHOLD
        tool_only = {
            "type": "tool_calls",
            "calls": [{"name": "run_shell", "arguments": {"command": "echo hi"}}],
            "assistant_message": {"role": "assistant", "content": ""},
        }
        result = await self._run_loop([tool_only] * _DOOM_LOOP_THRESHOLD)
        self.assertIn("循环保护", result)

    async def test_doom_loop_counter_resets_after_text_response(self):
        """Counter resets to 0 after a text response; a second streak below threshold also passes."""
        from executors.plugins.tool_loop_v1 import _DOOM_LOOP_THRESHOLD
        tool_only = {
            "type": "tool_calls",
            "calls": [{"name": "run_shell", "arguments": {"command": "echo hi"}, "id": "c1"}],
            "assistant_message": {"role": "assistant", "content": ""},
        }
        text_end = {"type": "text", "content": "end"}
        # THRESHOLD-1 tool calls, then text (resets), then THRESHOLD-1 more tool calls, then text end
        responses = ([tool_only] * (_DOOM_LOOP_THRESHOLD - 1) + [text_end]
                     + [tool_only] * (_DOOM_LOOP_THRESHOLD - 1) + [text_end])
        result = await self._run_loop(responses)
        self.assertEqual(result, "end")


# ---------------------------------------------------------------------------
# Skill path traversal protection (DFT-021)
# ---------------------------------------------------------------------------

class TestSkillPathTraversal(unittest.TestCase):
    """skill_path() defense-in-depth: reject unsafe names and out-of-base targets."""

    def test_rejects_parent_ref(self):
        import tempfile
        from pathlib import Path
        from skills.metadata import skill_path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "skills"
            base.mkdir()
            (Path(tmp) / "secret.md").write_text("top secret")  # exists, but outside base
            path, kind = skill_path(base, "../secret")
            self.assertIsNone(path)
            self.assertEqual(kind, "")

    def test_rejects_absolute_and_separators(self):
        from pathlib import Path
        from skills.metadata import skill_path
        for bad in ["/etc/hosts", "a/b", "a\\b", "..", "../x", "foo/../bar", " spaced"]:
            path, _ = skill_path(Path("/tmp/skills"), bad)
            self.assertIsNone(path, f"expected None for {bad!r}")

    def test_allows_legit_name(self):
        import tempfile
        from pathlib import Path
        from skills.metadata import skill_path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "skills"
            (base / "my-skill").mkdir(parents=True)
            (base / "my-skill" / "SKILL.md").write_text("---\nname: my-skill\n---\nhi")
            path, kind = skill_path(base, "my-skill")
            self.assertIsNotNone(path)
            self.assertEqual(kind, "md")


class TestRunSkillTraversal(unittest.IsolatedAsyncioTestCase):
    """run_skill() primary guard: only discovered skill names resolve."""

    def _make_ws(self, tmp):
        from pathlib import Path
        ws = Path(tmp)
        (ws / "skills" / "real").mkdir(parents=True)
        (ws / "skills" / "real" / "SKILL.md").write_text("---\nname: real\n---\nHELLO_BODY")
        (ws / "secret.md").write_text("PWNED")  # traversal target outside skills/
        return ws

    async def test_rejects_traversal_name(self):
        import tempfile
        from unittest.mock import patch
        from skills.loader import run_skill
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_ws(tmp)
            with patch("skills.loader.bot_ws", return_value=ws), \
                 patch("skills.discovery.bot_ws", return_value=ws):
                result = await run_skill(1, "../secret")
                self.assertIn("未找到技能", result)
                self.assertNotIn("PWNED", result)

    async def test_loads_discovered_skill(self):
        import tempfile
        from unittest.mock import patch
        from skills.loader import run_skill
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_ws(tmp)
            with patch("skills.loader.bot_ws", return_value=ws), \
                 patch("skills.discovery.bot_ws", return_value=ws):
                result = await run_skill(1, "real")
                self.assertIn("HELLO_BODY", result)


# ---------------------------------------------------------------------------
# run_shell sandbox tier 1: env allowlist + cwd confinement (DFT-023)
# ---------------------------------------------------------------------------

class TestRunShellSandboxEnv(unittest.TestCase):
    """_sandbox_env strips host secrets, keeps a minimal safe allowlist."""

    def test_strips_secrets_keeps_path(self):
        from unittest.mock import patch
        from executors.plugins.workspace_tools import _sandbox_env
        fake = {
            "PATH": "/usr/bin", "HOME": "/home/u", "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "OPENAI_API_KEY": "sk-secret", "AWS_SECRET_ACCESS_KEY": "x",
            "GITHUB_TOKEN": "ghp", "DATABASE_PASSWORD": "p",
        }
        with patch.dict("os.environ", fake, clear=True):
            env = _sandbox_env()
        self.assertIn("PATH", env)
        self.assertIn("HOME", env)
        self.assertIn("LANG", env)
        self.assertIn("LC_ALL", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("DATABASE_PASSWORD", env)


class TestRunShellCwdConfinement(unittest.TestCase):
    """_resolve_shell_cwd confines the working dir to the bot workspace."""

    def _resolve(self, cwd, bot_id, ws_root):
        from unittest.mock import patch
        from executors.plugins.workspace_tools import _resolve_shell_cwd
        with patch("workspace.bot_workspace", return_value=ws_root):
            return _resolve_shell_cwd(cwd, bot_id)

    def test_empty_cwd_defaults_to_workspace_root(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, err = self._resolve("", 1, root)
            self.assertEqual(err, "")
            self.assertEqual(path, root.resolve())

    def test_relative_cwd_resolves_under_workspace(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            path, err = self._resolve("sub", 1, root)
            self.assertEqual(err, "")
            self.assertEqual(path, (root / "sub").resolve())

    def test_dotdot_escape_rejected(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            root.mkdir()
            path, err = self._resolve("../", 1, root)
            self.assertIsNone(path)
            self.assertIn("越界", err)

    def test_absolute_outside_rejected(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            root.mkdir()
            path, err = self._resolve("/etc", 1, root)
            self.assertIsNone(path)
            self.assertIn("越界", err)

    def test_missing_bot_id_rejected(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path, err = self._resolve("", None, Path(tmp))
            self.assertIsNone(path)
            self.assertIn("bot_id", err)


# ---------------------------------------------------------------------------
# run_shell sandbox tier 2: permission backstop (DFT-023)
# ---------------------------------------------------------------------------

class TestRunShellTier2Guard(unittest.IsolatedAsyncioTestCase):
    """_default_shell_guard fails closed without a ruleset; denylist still applies."""

    async def test_blocks_when_no_ruleset(self):
        from executors.plugins.workspace_tools import _default_shell_guard
        result = await _default_shell_guard("run_shell", {"cmd": "ls"}, {})
        self.assertIsNotNone(result)
        self.assertTrue(result["block"])
        self.assertIn("ruleset", result["reason"])

    async def test_blocks_dangerous_with_ruleset(self):
        from executors.plugins.workspace_tools import _default_shell_guard
        result = await _default_shell_guard(
            "run_shell", {"cmd": "rm -rf /"}, {"ruleset": object()})
        self.assertIsNotNone(result)
        self.assertTrue(result["block"])

    async def test_allows_safe_with_ruleset(self):
        from executors.plugins.workspace_tools import _default_shell_guard
        result = await _default_shell_guard(
            "run_shell", {"cmd": "ls -la"}, {"ruleset": object()})
        self.assertIsNone(result)

    async def test_ignores_non_shell_tools(self):
        from executors.plugins.workspace_tools import _default_shell_guard
        result = await _default_shell_guard("read_file", {"path": "x"}, {})
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Permission hook fail-closed without a ruleset (DFT-024)
# ---------------------------------------------------------------------------

class TestPermissionHookFailClosed(unittest.IsolatedAsyncioTestCase):
    """When no ruleset is configured, approval-required tools must be denied,
    while read-only workspace tools stay allowed."""

    async def _check(self, name, args=None):
        from executors.plugins.workspace_tools import _permission_check_hook
        return await _permission_check_hook(name, args or {}, {})  # context has no ruleset

    async def test_blocks_run_shell(self):
        r = await self._check("run_shell", {"cmd": "ls"})
        self.assertIsNotNone(r)
        self.assertTrue(r["block"])

    async def test_blocks_write_file(self):
        r = await self._check("write_file", {"path": "a", "content": "b"})
        self.assertIsNotNone(r)
        self.assertTrue(r["block"])

    async def test_blocks_read_local_file(self):
        r = await self._check("read_local_file", {"path": "/etc/hosts"})
        self.assertIsNotNone(r)
        self.assertTrue(r["block"])

    async def test_blocks_write_local_file(self):
        r = await self._check("write_local_file", {"path": "/tmp/x", "content": "y"})
        self.assertIsNotNone(r)
        self.assertTrue(r["block"])

    async def test_blocks_spawn_agent(self):
        r = await self._check("spawn_agent", {"bot_name": "x", "task": "y"})
        self.assertIsNotNone(r)
        self.assertTrue(r["block"])

    async def test_allows_read_file(self):
        # workspace-confined read is safe even without a ruleset
        self.assertIsNone(await self._check("read_file", {"path": "a.txt"}))

    async def test_allows_list_workspace(self):
        self.assertIsNone(await self._check("list_workspace", {}))


# ---------------------------------------------------------------------------
# Extended sensitive-path coverage for local_file tools (DFT-023 remainder)
# ---------------------------------------------------------------------------

class TestSensitivePathExtended(unittest.TestCase):
    """Credential stores the architect flagged as exfiltration targets."""

    def _s(self, path: str) -> bool:
        from executors.plugins.workspace_tools import _is_sensitive_path
        return _is_sensitive_path(path)

    def test_git_credentials_blocked(self):
        self.assertTrue(self._s("~/.git-credentials"))

    def test_npmrc_blocked(self):
        self.assertTrue(self._s("~/.npmrc"))

    def test_pypirc_blocked(self):
        self.assertTrue(self._s("~/.pypirc"))

    def test_dockercfg_blocked(self):
        self.assertTrue(self._s("~/.dockercfg"))

    def test_docker_config_dir_blocked(self):
        self.assertTrue(self._s("~/.docker/config.json"))

    def test_gh_config_blocked(self):
        self.assertTrue(self._s("~/.config/gh/hosts.yml"))

    def test_password_store_blocked(self):
        self.assertTrue(self._s("~/.password-store/email.gpg"))

    def test_normal_config_file_allowed(self):
        # a non-credential file outside the flagged dirs stays allowed
        self.assertFalse(self._s("/home/user/project/settings.json"))


# ---------------------------------------------------------------------------
# react_v1 wires a ruleset into the tool execution context (DFT-024)
# ---------------------------------------------------------------------------

class TestReactV1RulesetWiring(unittest.IsolatedAsyncioTestCase):
    """react_v1 must pass a non-None ruleset to tool_executor so its tools are
    permission-checked (previously execution_ctx carried no ruleset → fail-open)."""

    async def test_execution_ctx_has_ruleset(self):
        from executors.plugins.react_v1 import ReactV1
        from executors.base import ExecutionContext

        captured = {}

        async def fake_execute(name, arguments, context=None):
            captured["context"] = context
            return "ok"

        # AI returns one Action, then a Final Answer.
        responses = [
            {"type": "text", "content": "Thought: go\nAction: list_workspace\nAction Input: {}"},
            {"type": "text", "content": "Thought: done\nFinal Answer: finished"},
        ]
        call_count = 0

        async def fake_call_ai_once(*a, **kw):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        bot = {
            "id": 5, "name": "R", "role": "dev", "avatar_color": "#fff", "type": "bot",
            "system_prompt": "s", "model_provider": "deepseek", "model_name": "deepseek-chat",
            "temperature": 0.7, "max_tokens": 4096, "executor_config": {},
        }
        ctx = ExecutionContext(
            bot=bot, group_id=1, user_message="do",
            sender={"id": 1, "name": "u", "type": "human"},
            history=[], all_bots=[bot], all_members=[bot],
            broadcaster=AsyncMock(),
        )

        m = "executors.plugins.react_v1."
        from unittest.mock import MagicMock
        fake_db = MagicMock()
        fake_db.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db.__aexit__ = AsyncMock(return_value=False)

        with patch(m + "call_ai_once", new=fake_call_ai_once), \
             patch(m + "tool_executor.execute", new=fake_execute), \
             patch(m + "get_memory_context", new=AsyncMock(return_value="")), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch(m + "format_context_blocks", return_value=""), \
             patch(m + "list_skills_all", return_value=[]), \
             patch(m + "write_connect", return_value=fake_db), \
             patch(m + "save_message", new=AsyncMock(return_value=1)), \
             patch(m + "get_messages", new=AsyncMock(return_value=[{"created_at": "now"}])), \
             patch(m + "add_to_chroma", new=AsyncMock()), \
             patch(m + "maybe_summarize", new=AsyncMock()), \
             patch(m + "append_log", new=AsyncMock()), \
             patch("permissions.load_rules", new=AsyncMock(return_value=[])):
            await ReactV1().run(ctx)

        self.assertIn("context", captured)
        self.assertIsNotNone(captured["context"].get("ruleset"),
                             "react_v1 passed no ruleset to tool_executor (fail-open)")
