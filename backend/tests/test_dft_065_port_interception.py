import asyncio
import unittest
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.workspace_tools import _handle_run_shell

class TestDFT065PortInterception(unittest.IsolatedAsyncioTestCase):
    async def test_port_interception_boundaries(self):
        # We need to mock _resolve_shell_cwd and other things, but we can also
        # just test the logic if we export it.
        # Since it is internal, let's use a trick or just check the resulting command
        # if we can. 
        # Actually, let's mock asyncio.create_subprocess_exec to see what command it gets.
        
        from unittest.mock import patch, AsyncMock
        
        async def mock_exec(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"stdout", b"stderr")
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec) as mock_run:
            with patch("executors.plugins.workspace_tools._resolve_shell_cwd", return_value=("/tmp", None)):
                # 1. head -80 should NOT be intercepted
                await _handle_run_shell("head -80 file.txt", context={"bot_id": 1})
                cmd_called = mock_run.call_args[0][2] if sys.platform != "win32" else mock_run.call_args[0][1]
                # cmd_called will have ulimit prefix on non-windows
                self.assertIn("head -80", cmd_called)
                
                # 2. --port 80 SHOULD be intercepted
                mock_run.reset_mock()
                await _handle_run_shell("npm start -- --port 80", context={"bot_id": 1})
                cmd_called = mock_run.call_args[0][2] if sys.platform != "win32" else mock_run.call_args[0][1]
                self.assertNotIn("--port 80", cmd_called)
                self.assertTrue(re.search(r"--port \d{4,5}", cmd_called))

                # 3. 180 should NOT be intercepted
                mock_run.reset_mock()
                await _handle_run_shell("echo 180", context={"bot_id": 1})
                cmd_called = mock_run.call_args[0][2] if sys.platform != "win32" else mock_run.call_args[0][1]
                self.assertIn("echo 180", cmd_called)

if __name__ == "__main__":
    unittest.main()
