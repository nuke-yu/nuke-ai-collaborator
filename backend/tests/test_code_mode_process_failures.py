from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from runtime_features.code_mode.adapters import SubprocessCodeExecutionAdapter
from runtime_features.code_mode.domain import CodeModeLimits, CodeModeRejected


class CodeModeProcessFailureTest(unittest.TestCase):
    def test_eof_from_dead_child_becomes_code_mode_rejection(self) -> None:
        parent = MagicMock()
        parent.poll.return_value = True
        parent.recv.side_effect = EOFError()
        child = MagicMock()
        process = MagicMock()
        process.is_alive.return_value = False
        process.exitcode = -9

        context = MagicMock()
        context.Pipe.return_value = (parent, child)
        context.Process.return_value = process

        with patch("runtime_features.code_mode.adapters.multiprocessing.get_context", return_value=context):
            with self.assertRaisesRegex(CodeModeRejected, "子进程异常退出"):
                SubprocessCodeExecutionAdapter().execute(
                    "print(1)", object(), CodeModeLimits(timeout_seconds=1)
                )

        process.start.assert_called_once()
        process.join.assert_called_once()
        parent.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
