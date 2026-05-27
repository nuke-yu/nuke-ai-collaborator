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
