"""Per-group Docker execution sandbox — the substrate behind ContainerShellBackend.

One long-lived container per active group (`sleep infinity`); the worker
`docker exec`s bot commands into it. Isolation is by MOUNT: only that group's
workspace is bind-mounted (at the same host path), so sibling groups / central
DB / host secrets are absent from the container. The container runs as the host
uid so it can read/write the bind-mounted workspace while staying confined.

Design notes:
- docker is driven via the `docker` CLI (simple, mockable). Production should
  front the daemon with a socket-proxy rather than expose /var/run/docker.sock.
- in-container timeout uses coreutils `timeout` (exit 124) so the *real* process
  is killed, not just the local `docker exec` client.
- every real docker invocation funnels through ContainerManager._docker(), the
  single seam tests mock; the argv builders are pure.
"""
from __future__ import annotations

import asyncio
import os

from core import config

_CONTAINER_PREFIX = "nuke-sbx-"
_SHELL = "/bin/sh"


def container_name(group_id: int) -> str:
    return f"{_CONTAINER_PREFIX}{group_id}"


def build_run_argv(
    group_id: int,
    workspace_dir: str,
    *,
    image: str,
    uid: int,
    gid: int,
    memory: str,
    cpus: str,
    network: str,
) -> list[str]:
    """Pure: `docker run` argv for a group's idle sandbox.

    Mounts ONLY workspace_dir, at the same path inside the container, so a host
    work_dir under it is valid inside without translation."""
    ws = str(workspace_dir)
    return [
        "docker", "run", "-d", "--rm",
        "--name", container_name(group_id),
        "--user", f"{uid}:{gid}",
        "--mount", f"type=bind,src={ws},dst={ws}",
        "--workdir", ws,
        f"--memory={memory}",
        f"--cpus={cpus}",
        f"--network={network}",
        "--label", "app=nuke-sandbox",
        image, "sleep", "infinity",
    ]


def build_exec_argv(
    group_id: int,
    *,
    cwd: str,
    env: dict,
    cmd: str,
    timeout: int | None = None,
    detach: bool = False,
) -> list[str]:
    """Pure: `docker exec` argv. `timeout` wraps the in-container command with
    coreutils `timeout` so the real process is killed (exit 124) on expiry."""
    argv = ["docker", "exec"]
    if detach:
        argv.append("-d")
    argv += ["-w", cwd]
    for k, v in (env or {}).items():
        argv += ["-e", f"{k}={v}"]
    argv.append(container_name(group_id))
    if timeout:
        argv += ["timeout", str(timeout)]
    argv += [_SHELL, "-c", cmd]
    return argv


class ContainerManager:
    """Owns the group → container lifecycle. Real docker calls go through
    _docker() (the only thing tests mock); everything else is logic."""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def _docker(self, argv: list[str], timeout: float | None = None) -> tuple[int, str, str]:
        """Run a docker CLI command → (returncode, stdout, stderr). Isolated seam."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return 127, "", "docker CLI not found"
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return 124, "", "docker call timed out"
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")

    async def available(self) -> bool:
        # NB: `docker info --format ...` exits 0 even when the daemon is down
        # (it just renders an empty ServerVersion). So require non-empty output,
        # not just rc==0 — otherwise 'auto' mode false-positives and never falls
        # back to local.
        rc, out, _ = await self._docker(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
        return rc == 0 and bool(out.strip())

    async def _is_running(self, group_id: int) -> bool:
        rc, out, _ = await self._docker(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name(group_id)],
            timeout=10,
        )
        return rc == 0 and out.strip() == "true"

    async def ensure(self, group_id: int, workspace_dir) -> None:
        """Idempotently start the group's sandbox container."""
        async with self._lock:
            if await self._is_running(group_id):
                return
            argv = build_run_argv(
                group_id, str(workspace_dir),
                image=config.SANDBOX_IMAGE,
                uid=os.getuid(), gid=os.getgid(),
                memory=config.SANDBOX_MEMORY,
                cpus=config.SANDBOX_CPUS,
                network=config.SANDBOX_NETWORK,
            )
            rc, _, err = await self._docker(argv, timeout=60)
            if rc != 0:
                raise RuntimeError(f"sandbox 容器启动失败 (group {group_id}): {err.strip() or rc}")

    async def exec_foreground(
        self, group_id: int, *, cmd: str, cwd: str, env: dict, timeout: int
    ) -> tuple[int | None, str, str, bool]:
        """→ (exit_code, stdout, stderr, timed_out)."""
        argv = build_exec_argv(group_id, cwd=cwd, env=env, cmd=cmd, timeout=timeout)
        # outer wait is a backstop; in-container `timeout` is the primary guard
        rc, out, err = await self._docker(argv, timeout=timeout + 10)
        if rc == 124:
            return None, "", "", True
        return rc, out.strip(), err.strip(), False

    async def exec_background(
        self, group_id: int, *, cmd: str, cwd: str, env: dict
    ) -> str:
        """Detached exec; returns a short identifier note (docker exec -d gives no pid)."""
        argv = build_exec_argv(group_id, cwd=cwd, env=env, cmd=cmd, detach=True)
        rc, out, err = await self._docker(argv, timeout=30)
        if rc != 0:
            raise RuntimeError(f"后台 exec 失败: {err.strip() or rc}")
        return f"{container_name(group_id)} (detached)"

    async def stop(self, group_id: int) -> None:
        await self._docker(["docker", "stop", container_name(group_id)], timeout=30)
