"""run_shell execution-backend seam.

Splits *where a command runs and what filesystem it can see* out of the
orchestration in `_handle_run_shell`. The orchestrator builds a normalized
ShellExecRequest and hands it to a ShellExecBackend; the chosen backend decides
the isolation strength:

  - LocalShellBackend     — host subprocess, NO cross-group isolation (dev).
  - ContainerShellBackend — per-group sandbox container that bind-mounts ONLY
    that group's workspace, so group isolation becomes a mount fact (prod).

This module is pure interface + data types — it imports nothing from
workspace_tools, so the concrete backends can live there (next to _DEFAULT_SHELL
etc.) without an import cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ShellExecRequest:
    """Everything a backend needs to run one command. `cmd` is the final command
    after port interception; mem-limit is NOT pre-applied — each backend enforces
    it its own way (local: ulimit wrap; container: cgroup)."""
    cmd: str
    work_dir: Path                 # host absolute path inside the group workspace
    env: dict                      # already secret-stripped (tier-1 sandbox_env)
    group_id: int | None           # selects the per-group sandbox (container backend)
    bot_id: int | None
    mem_limit_bytes: int
    timeout_s: int                 # foreground only


@dataclass(frozen=True)
class ShellExecResult:
    """Foreground outcome."""
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class ShellBackgroundHandle:
    """Started long-running (background) process descriptor."""
    identifier: str                # local: pid; container: exec/container id
    note: str = ""


@runtime_checkable
class ShellExecBackend(Protocol):
    async def ensure_ready(self, group_id: int | None) -> None:
        """Container backend: lazily spin up / pre-warm group_G's sandbox.
        Local backend: no-op."""
        ...

    async def run_foreground(self, req: ShellExecRequest) -> ShellExecResult:
        """Run the command, capture output, own its timeout + kill-on-cancel."""
        ...

    async def start_background(self, req: ShellExecRequest) -> ShellBackgroundHandle:
        """Start a background process and return immediately."""
        ...

    async def healthy(self) -> bool:
        """Whether this backend can execute right now — drives 'auto' selection
        and the production fail-closed decision."""
        ...
