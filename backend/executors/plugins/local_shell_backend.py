"""Host subprocess shell execution adapter (development/trusted mode)."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from executors.plugins.shell_backend import ShellExecBackend, ShellExecRequest, ShellExecResult, ShellBackgroundHandle


class LocalShellBackend:
    """Host subprocess backend; it provides no cross-group isolation."""

    def __init__(self, *, wrap_command: Callable[[str, int], str], safe_kill: Callable[[object], None],
                 shell: Sequence[str], is_windows: bool, memory_limiter):
        self._wrap_command = wrap_command
        self._safe_kill = safe_kill
        self._shell = tuple(shell)
        self._is_windows = is_windows
        self._memory_limiter = memory_limiter

    async def ensure_ready(self, group_id) -> None:
        return

    async def healthy(self) -> bool:
        return True

    async def run_foreground(self, req: ShellExecRequest) -> ShellExecResult:
        safe_cmd = self._wrap_command(req.cmd, req.mem_limit_bytes)
        proc = await asyncio.create_subprocess_exec(
            *self._shell,
            safe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(req.work_dir),
            env=req.env,
        )
        if self._is_windows:
            self._memory_limiter.apply_memory_limit(proc.pid, req.mem_limit_bytes)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=req.timeout_s)
        except asyncio.TimeoutError:
            self._safe_kill(proc)
            return ShellExecResult(None, "", "", timed_out=True)
        except asyncio.CancelledError:
            self._safe_kill(proc)
            raise
        return ShellExecResult(
            proc.returncode,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )

    async def start_background(self, req: ShellExecRequest) -> ShellBackgroundHandle:
        safe_cmd = self._wrap_command(req.cmd, req.mem_limit_bytes)
        proc = await asyncio.create_subprocess_exec(
            *self._shell,
            safe_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(req.work_dir),
            env=req.env,
            start_new_session=not self._is_windows,
        )
        if self._is_windows:
            self._memory_limiter.apply_memory_limit(proc.pid, req.mem_limit_bytes)
        return ShellBackgroundHandle(identifier=str(proc.pid))
