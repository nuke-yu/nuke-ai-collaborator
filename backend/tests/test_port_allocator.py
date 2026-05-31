import os
import sys
import unittest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.workspace_tools import _handle_run_shell

class TestPortAllocator(unittest.IsolatedAsyncioTestCase):
    async def test_run_shell_intercepts_8080(self):
        ctx = {"bot_id": 1}
        
        with patch("executors.plugins.workspace_tools._resolve_shell_cwd", return_value=("", "")), \
             patch("executors.plugins.workspace_tools._allocate_free_port", return_value=49152):
            
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_proc.returncode = 0
            
            with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
                # Command with hardcoded port 8080
                result = await _handle_run_shell("python app.py --port 8080", context=ctx)
                
                # 1. Verify physical replacement in command
                call_args = mock_exec.call_args[0]
                # call_args: (*_DEFAULT_SHELL, safe_cmd)
                # safe_cmd is the last arg
                actual_cmd = call_args[-1]
                self.assertIn("--port 49152", actual_cmd)
                self.assertNotIn("--port 8080", actual_cmd)
                
                # 2. Verify result message
                self.assertIn("[安全拦截] 已将硬编码端口 8080 替换为动态端口 49152", result)

    # ---- DFT-065: word-boundary matching, not raw substring ----
    async def _run(self, cmd: str):
        """Run _handle_run_shell with a mocked subprocess; return (safe_cmd, result)."""
        with patch("executors.plugins.workspace_tools._resolve_shell_cwd", return_value=("", "")), \
             patch("executors.plugins.workspace_tools._allocate_free_port", return_value=49152):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_proc.returncode = 0
            with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
                result = await _handle_run_shell(cmd, context={"bot_id": 1})
                return mock_exec.call_args[0][-1], result

    async def test_port_digits_inside_a_token_not_intercepted(self):
        # '8000' is a substring of '18000' — the old `if p in cmd` corrupted this.
        safe_cmd, result = await self._run("cat report18000.log")
        self.assertIn("18000", safe_cmd)        # command left untouched
        self.assertNotIn("49152", safe_cmd)     # nothing was replaced
        self.assertNotIn("[安全拦截]", result)

    async def test_8080_matched_whole_not_double_replaced_via_80(self):
        # Desc-sort + word boundary: 8080 matches as a whole; the inner '80' is
        # not separately substituted (old substring replace produced port+port).
        safe_cmd, result = await self._run("curl http://localhost:8080/health")
        self.assertIn("49152", safe_cmd)
        self.assertNotIn("8080", safe_cmd)
        self.assertNotIn("4915249152", safe_cmd)
        self.assertIn("8080", result)           # reported as 8080, not 80

    async def test_standalone_dev_port_still_intercepted(self):
        safe_cmd, result = await self._run("python -m http.server 8000")
        self.assertIn("http.server 49152", safe_cmd)
        self.assertNotIn("http.server 8000", safe_cmd)
        self.assertIn("[安全拦截]", result)


if __name__ == "__main__":
    unittest.main()
