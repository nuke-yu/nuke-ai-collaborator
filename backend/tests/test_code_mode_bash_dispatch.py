from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from executors.plugins.workspace_tools import _handle_run_code


class CodeModeBashDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_reenters_tool_executor_authorization_chain(self):
        dispatched = AsyncMock(return_value=("exit_code: 0", False))
        context = {
            "bot_id": 7,
            "group_id": 3,
            "session_id": "session-1",
            "ruleset": object(),
        }
        with patch("executors.tool_executor.execute", new=dispatched):
            result = await _handle_run_code("print(tools.bash('pytest -q'))", context)

        self.assertIn("exit_code: 0", result)
        dispatched.assert_awaited_once()
        name, arguments = dispatched.await_args.args[:2]
        self.assertEqual(name, "run_shell")
        self.assertEqual(arguments["cmd"], "pytest -q")
        self.assertIs(dispatched.await_args.kwargs["context"], context)
