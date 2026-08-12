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


class TestSensitivePathExtended(unittest.TestCase):
    """DFT-023: expanded blacklist for credentials."""

    def _s(self, p):
        from executors.plugins.workspace_tools import _is_sensitive_path
        return _is_sensitive_path(p)

    def test_docker_config_blocked(self):
        self.assertTrue(self._s("~/.dockercfg"))
        self.assertTrue(self._s("/any/.docker/config.json"))

    def test_gh_token_blocked(self):
        self.assertTrue(self._s("~/.config/gh/hosts.yml"))

    def test_pass_store_blocked(self):
        self.assertTrue(self._s("~/.password-store/secret.gpg"))

    def test_keystore_blocked(self):
        self.assertTrue(self._s("/project/android.keystore"))
        self.assertTrue(self._s("/project/upload.jks"))

    def test_cookie_db_blocked(self):
        self.assertTrue(self._s("/home/user/Library/Cookies/cookies.sqlite"))

    def test_git_credentials_blocked(self):
        self.assertTrue(self._s("~/.git-credentials"))

    def test_npmrc_blocked(self):
        self.assertTrue(self._s("/p/.npmrc"))

    def test_normal_json_allowed(self):
        self.assertFalse(self._s("/home/user/project/settings.json"))


# ---------------------------------------------------------------------------
# Doom Loop Guard
# ---------------------------------------------------------------------------

class TestDoomLoopGuard(unittest.IsolatedAsyncioTestCase):
    """DFT-003 / DFT-035 doom loop protection in tool_loop_v1."""

    async def asyncSetUp(self):
        import db as _db_mod
        from db.schema import init_db
        import tempfile
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_db = os.path.join(self.tmp_dir.name, "test_p1_safety.db")
        self._orig = _db_mod.DB_PATH
        _db_mod.DB_PATH = self.test_db
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(self.test_db) as db:
            await run_migrations(db)
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'g')")
            await db.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (1, 1, 'bot', 'bot', 'dev')")
            await db.commit()

    async def asyncTearDown(self):
        import db as _db_mod
        _db_mod.DB_PATH = self._orig
        self.tmp_dir.cleanup()

    async def test_consecutive_tool_only_loop_terminates(self):
        from executors.plugins.tool_loop_v1 import ToolLoopV1
        from executors.base import ExecutionContext

        bot = {
            "id": 1, "name": "bot", "role": "dev", "avatar_color": "#fff",
            "type": "bot", "system_prompt": "s", "model_provider": "deepseek",
            "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096,
            "executor_config": {},
        }
        ctx = ExecutionContext(
            bot=bot, group_id=1, user_message="task",
            sender={"id": 2, "name": "Human", "type": "human"},
            history=[], all_bots=[bot], all_members=[bot],
        interaction=AsyncMock(),
        )

        # AI returns tool_calls ONLY, for more than _DOOM_LOOP_THRESHOLD times
        async def mock_call(*a, **kw):
            return {
                "type": "tool_calls",
                "calls": [{"id": "tc1", "name": "read_file", "arguments": {"path": "a.txt"}}],
                "assistant_message": {"role": "assistant", "content": "calling", "tool_calls": []},
                "usage": {}
            }

        m = "executors.plugins.tool_loop_v1."
        with patch("core.orchestration.ai_service.call_ai_once", new=AsyncMock(side_effect=mock_call)), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch(m + "list_skills_all", return_value=[]), \
             patch(m + "load_always_skills", return_value=[]), \
             patch(m + "append_log", new=AsyncMock()), \
             patch("executors.plugins.tool_loop_v1.tool_executor.execute", new=AsyncMock(return_value=("result", False))), \
             patch("executors.plugins.tool_loop_v1.tool_executor.get_schemas", return_value=[{"function": {"name": "read_file"}}]):

            executor = ToolLoopV1()
            result = await executor.run(ctx)

        self.assertIn("[循环保护]", result.full_text)


if __name__ == "__main__":
    unittest.main()
