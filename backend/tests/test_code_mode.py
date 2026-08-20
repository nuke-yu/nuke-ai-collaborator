from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import workspace
from executors.code_mode import CodeModeRejected, run_code


class CodeModeTest(unittest.TestCase):
    def test_executes_control_flow_without_general_builtins(self) -> None:
        result = run_code(
            "total = 0\nfor item in range(4):\n    total += item\nprint(total)",
            bot_id=7, group_id=3, session_id="code-session",
        )
        self.assertEqual(result, "6\n")

    def test_rejects_import_and_arbitrary_attribute_access(self) -> None:
        for code in (
            "import os", "print((1).__class__)", "open('x')",
            "print([x for x in range(10)])", "print('x' * 100001)",
        ):
            with self.subTest(code=code), self.assertRaises(CodeModeRejected):
                run_code(code, bot_id=7, group_id=3, session_id="code-session")

    def test_workspace_sdk_preserves_read_before_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "sample.txt"
            path.write_text("one\ntwo\n", encoding="utf-8")
            with patch.object(workspace, "_get_effective_ws", return_value=(root, "sample.txt")):
                result = run_code(
                    "text = tools.read('sample.txt')\n"
                    "print(tools.write('sample.txt', 'ONE\\ntwo\\n'))",
                    bot_id=7, group_id=3, session_id="code-session",
                )
            self.assertTrue("已写入" in result or "已修改" in result)
            self.assertEqual(path.read_text(encoding="utf-8"), "ONE\ntwo\n")

    def test_requires_group_scope(self) -> None:
        with self.assertRaises(CodeModeRejected):
            run_code("print(1)", bot_id=7, group_id=None, session_id="code-session")

    def test_bash_uses_existing_sandbox_and_blocks_dangerous_commands(self) -> None:
        async def fake_shell(*_args, **_kwargs):
            return "exit_code: 0\\nstdout:\\npytest"

        with patch(
            "executors.plugins.workspace_tools._handle_run_shell",
            new=AsyncMock(side_effect=fake_shell),
        ):
            result = run_code(
                "print(tools.bash('pytest -q'))",
                bot_id=7, group_id=3, session_id="code-session",
            )
        self.assertIn("exit_code: 0", result)
        with self.assertRaises(CodeModeRejected):
            run_code("tools.bash('rm -rf /')", bot_id=7, group_id=3, session_id="code-session")


if __name__ == "__main__":
    unittest.main()
