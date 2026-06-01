"""CELL-23: Windows Job Object sandbox tests."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins import win_sandbox

class TestCell23WinSandbox(unittest.TestCase):
    def test_apply_memory_limit(self):
        # We can only truly test this on Windows, but we can verify it doesn't crash on Mac
        if sys.platform != "win32":
            win_sandbox.apply_memory_limit(12345, 1000)
            # Should just return silently
            self.assertTrue(True)
        else:
            # If we run this test on a Windows CI runner, we mock the kernel32 calls
            with patch('executors.plugins.win_sandbox.kernel32', create=True) as mock_kernel32:
                mock_kernel32.CreateJobObjectW.return_value = 123
                mock_kernel32.SetInformationJobObject.return_value = True
                mock_kernel32.OpenProcess.return_value = 456
                mock_kernel32.AssignProcessToJobObject.return_value = True
                
                win_sandbox.apply_memory_limit(9999, 1024 * 1024)
                
                mock_kernel32.CreateJobObjectW.assert_called_once()
                mock_kernel32.SetInformationJobObject.assert_called_once()
                mock_kernel32.OpenProcess.assert_called_once()
                mock_kernel32.AssignProcessToJobObject.assert_called_once()

if __name__ == "__main__":
    unittest.main()
