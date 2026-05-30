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

if __name__ == "__main__":
    unittest.main()
