import os
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.workspace_tools import (
    _handle_run_shell, _check_shell_command_paths, _worktree_lock_for, set_shell_backend_for_test,
)
from executors.plugins.shell_backend import (
    ShellExecResult, ShellBackgroundHandle,
)

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

    async def test_run_shell_timeout_logs_when_kill_fails(self):
        ctx = {"bot_id": 1}

        with patch("executors.plugins.workspace_tools._resolve_shell_cwd", return_value=("", "")):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.kill = MagicMock(side_effect=RuntimeError("kill failed"))

            with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)), \
                 self.assertLogs("executors.plugins.workspace_tools", level="ERROR") as logs:
                result = await _handle_run_shell("sleep 10", timeout=1, context=ctx)

            self.assertIn("[安全拦截]", result)
            self.assertIn("1 秒", result)
            self.assertTrue(any("failed to kill timed-out process" in line for line in logs.output))

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

    def test_shell_path_validation_logs_when_resolve_fails(self):
        work_dir = Path("/ws/group_1")

        class BadPath:
            def __str__(self):
                return "/Users/nuke/bad"

            def expanduser(self):
                return self

            def resolve(self):
                raise RuntimeError("resolve failed")

        with patch("executors.plugins.workspace_tools.re.findall", return_value=["/Users/nuke/bad"]), \
             patch("executors.plugins.workspace_tools.Path", side_effect=lambda value: BadPath() if value == "/Users/nuke/bad" else Path(value)), \
             self.assertLogs("executors.plugins.workspace_tools", level="ERROR") as logs:
            result = _check_shell_command_paths("cat /Users/nuke/bad", work_dir)

        self.assertIsNone(result)
        self.assertTrue(any("failed to validate shell path candidate" in line for line in logs.output))

    def test_shell_home_path_validation_logs_when_resolve_fails(self):
        work_dir = Path("/ws/group_1")
        home = str(Path("~").expanduser().resolve())

        class BadHomePath:
            def __str__(self):
                return home

            def expanduser(self):
                return self

            def resolve(self):
                raise RuntimeError("resolve failed")

        with patch("executors.plugins.workspace_tools.re.split", return_value=[home]), \
             patch("executors.plugins.workspace_tools.Path", side_effect=lambda value: BadHomePath() if value == home else Path(value)), \
             self.assertLogs("executors.plugins.workspace_tools", level="ERROR") as logs:
            result = _check_shell_command_paths(f"echo {home}", work_dir)

        self.assertIsNone(result)
        self.assertTrue(any("failed to validate shell home-path candidate" in line for line in logs.output))

    def test_worktree_lock_resolution_logs_when_group_workspace_fails(self):
        work_dir = Path("/ws/group_1/workspace/project")

        with patch("executors.plugins.workspace_tools._ws.group_workspace", side_effect=OSError("workspace unavailable")), \
             self.assertLogs("executors.plugins.workspace_tools", level="ERROR") as logs:
            result = _worktree_lock_for(work_dir, group_id=1)

        self.assertIsNone(result)
        self.assertTrue(any("failed to resolve worktree lock" in line for line in logs.output))

class _FakeBackend:
    """Records the request and returns canned outcomes — lets us test the
    orchestrator (_handle_run_shell) independently of how a command runs."""
    def __init__(self, *, foreground=None, background=None, ensure_exc=None):
        self._foreground = foreground
        self._background = background
        self._ensure_exc = ensure_exc
        self.seen = None
        self.ensure_called_with = "UNSET"

    async def ensure_ready(self, group_id):
        self.ensure_called_with = group_id
        if self._ensure_exc:
            raise self._ensure_exc

    async def healthy(self):
        return True

    async def run_foreground(self, req):
        self.seen = req
        return self._foreground

    async def start_background(self, req):
        self.seen = req
        return self._background


class TestBackendSeam(unittest.IsolatedAsyncioTestCase):
    """The orchestrator dispatches to the selected ShellExecBackend; isolation
    strength is the backend's concern, output formatting is the orchestrator's."""

    def tearDown(self):
        set_shell_backend_for_test(None)   # drop the injected backend / cache

    async def _run(self, fake, cmd="echo hi", **kw):
        from pathlib import Path
        set_shell_backend_for_test(fake)
        with patch("executors.plugins.workspace_tools._resolve_shell_cwd",
                   return_value=(Path("/ws/group_1"), "")), \
             patch("executors.plugins.workspace_tools._worktree_lock_for",
                   return_value=None):
            return await _handle_run_shell(cmd, context={"bot_id": 1, "group_id": 1}, **kw)

    async def test_foreground_result_formatting(self):
        fake = _FakeBackend(foreground=ShellExecResult(0, "hello", ""))
        out = await self._run(fake)
        self.assertIn("exit_code: 0", out)
        self.assertIn("stdout:\nhello", out)
        # request carried the group_id so a container backend can pick group_1's sandbox
        self.assertEqual(fake.seen.group_id, 1)
        self.assertEqual(fake.ensure_called_with, 1)

    async def test_background_returns_handle_identifier(self):
        fake = _FakeBackend(background=ShellBackgroundHandle("99999"))
        out = await self._run(fake, background=True)
        self.assertIn("PID: 99999", out)

    async def test_timeout_from_backend_maps_to_message(self):
        fake = _FakeBackend(foreground=ShellExecResult(None, "", "", timed_out=True))
        out = await self._run(fake, timeout=7)
        self.assertIn("[安全拦截]", out)
        self.assertIn("7 秒", out)

    async def test_backend_unavailable_fails_closed(self):
        # container 'required' but unhealthy → ensure_ready raises → command does
        # NOT run, surfaced as a system error (fail closed, never silent local run).
        fake = _FakeBackend(ensure_exc=NotImplementedError("no docker"))
        out = await self._run(fake)
        self.assertIn("[系统错误]", out)
        self.assertIsNone(fake.seen)        # never reached run_foreground


if __name__ == "__main__":
    unittest.main()
