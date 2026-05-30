import os
import sys
import unittest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.workspace_tools import _handle_run_shell

class TestProcessSandbox(unittest.IsolatedAsyncioTestCase):
    async def test_run_shell_timeout_kills_process(self):
        ctx = {"bot_id": 1}
        
        # We'll patch _resolve_shell_cwd so we don't need real directories
        with patch("executors.plugins.workspace_tools._resolve_shell_cwd", return_value=("", "")):
            # Patch create_subprocess_exec to return a mock process that hangs
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.kill = MagicMock()
            
            with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
                # Request a 1-second timeout
                result = await _handle_run_shell("sleep 10", timeout=1, context=ctx)
                
                # Verify timeout logic was triggered
                self.assertIn("[安全拦截]", result)
                self.assertIn("1 秒", result)
                # Verify kill was called to prevent zombie process
                mock_proc.kill.assert_called_once()

    async def test_run_shell_enforces_max_timeout_ceiling(self):
        ctx = {"bot_id": 1}
        
        with patch("executors.plugins.workspace_tools._resolve_shell_cwd", return_value=("", "")):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.kill = MagicMock()
            
            with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
                # Bot requests 9999 seconds (malicious or buggy)
                result = await _handle_run_shell("sleep 9999", timeout=9999, context=ctx)
                
                # Verify it was capped at 300 seconds
                self.assertIn("300 秒", result)
                mock_proc.kill.assert_called_once()

if __name__ == "__main__":
    unittest.main()
