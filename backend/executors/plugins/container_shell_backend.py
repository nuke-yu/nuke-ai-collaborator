"""Container-backed shell execution adapter."""
from __future__ import annotations

from executors.plugins.shell_backend import ShellExecBackend, ShellExecRequest, ShellExecResult, ShellBackgroundHandle
import workspace as _ws


class ContainerShellBackend:
    """Per-group container backend; isolation is enforced by mount boundaries."""

    def __init__(self, manager=None):
        from executors.plugins import container_sandbox

        self._mgr = manager or container_sandbox.ContainerManager()

    async def ensure_ready(self, group_id) -> None:
        if group_id is None:
            raise RuntimeError("container backend 需要 group_id（无群上下文无法隔离）")
        await self._mgr.ensure(group_id, _ws.group_workspace(group_id))

    async def healthy(self) -> bool:
        return await self._mgr.available()

    async def run_foreground(self, req: ShellExecRequest) -> ShellExecResult:
        rc, out, err, timed_out = await self._mgr.exec_foreground(
            req.group_id,
            cmd=req.cmd,
            cwd=str(req.work_dir),
            env=req.env,
            timeout=req.timeout_s,
        )
        return ShellExecResult(rc, out, err, timed_out=timed_out)

    async def start_background(self, req: ShellExecRequest) -> ShellBackgroundHandle:
        ident = await self._mgr.exec_background(
            req.group_id,
            cmd=req.cmd,
            cwd=str(req.work_dir),
            env=req.env,
        )
        return ShellBackgroundHandle(identifier=ident)
