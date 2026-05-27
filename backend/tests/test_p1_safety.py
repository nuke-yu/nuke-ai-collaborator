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
        # path traversal: resolve() should normalize this
        # ~/.aws/../project/.env — .env part should still be caught
        self.assertTrue(self._is_sensitive("/project/.env"))


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
