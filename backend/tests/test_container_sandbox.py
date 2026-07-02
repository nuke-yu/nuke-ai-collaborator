"""Tests for the per-group container sandbox (Phase 2 substrate).

No live Docker daemon needed: the pure argv builders are tested directly, and
the single real-docker seam (ContainerManager._docker) is mocked. Real
end-to-end against a daemon is a separate manual step.
"""
import os
import sys
import time
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins import container_sandbox as cs
from executors.plugins.workspace_tools import ContainerShellBackend
from executors.plugins.shell_backend import ShellExecRequest


class TestBuildRunArgv(unittest.TestCase):
    def test_mounts_only_group_dir_same_path(self):
        argv = cs.build_run_argv(
            3, "/var/lib/nuke/workspaces/group_3",
            image="nuke-sandbox:latest", uid=1001, gid=1001,
            memory="512m", cpus="2", network="bridge",
        )
        joined = " ".join(argv)
        # only the group's dir is bind-mounted, identical src and dst (no translation)
        self.assertIn(
            "--mount type=bind,src=/var/lib/nuke/workspaces/group_3,"
            "dst=/var/lib/nuke/workspaces/group_3",
            joined,
        )
        # no other workspace/group path leaks into the mount set
        self.assertEqual(joined.count("--mount"), 1)
        self.assertNotIn("group_2", joined)
        self.assertNotIn("chat.db", joined)

    def test_runs_as_host_uid_with_limits(self):
        argv = cs.build_run_argv(
            3, "/ws/group_3", image="img", uid=1001, gid=1001,
            memory="256m", cpus="1.5", network="none",
        )
        self.assertIn("--user", argv)
        self.assertEqual(argv[argv.index("--user") + 1], "1001:1001")
        self.assertIn("--memory=256m", argv)
        self.assertIn("--cpus=1.5", argv)
        self.assertIn("--network=none", argv)
        self.assertEqual(argv[-3:], ["img", "sleep", "infinity"])
        self.assertIn("nuke-sbx-3", argv)


class TestBuildExecArgv(unittest.TestCase):
    def test_foreground_wraps_timeout_and_passes_env_cwd(self):
        argv = cs.build_exec_argv(
            5, cwd="/ws/group_5/workspace/repo",
            env={"PATH": "/usr/bin", "HOME": "/tmp"}, cmd="pytest -q", timeout=30,
        )
        self.assertEqual(argv[:2], ["docker", "exec"])
        self.assertIn("-w", argv)
        self.assertEqual(argv[argv.index("-w") + 1], "/ws/group_5/workspace/repo")
        self.assertIn("-e", argv)
        self.assertIn("PATH=/usr/bin", argv)
        # in-container `timeout` kills the real process; sh -c carries the command
        self.assertEqual(argv[-5:], ["timeout", "30", "/bin/sh", "-c", "pytest -q"])
        self.assertIn("nuke-sbx-5", argv)

    def test_background_detaches_and_no_timeout(self):
        argv = cs.build_exec_argv(5, cwd="/ws", env={}, cmd="npm start", detach=True)
        self.assertIn("-d", argv)
        self.assertNotIn("timeout", argv)
        self.assertEqual(argv[-3:], ["/bin/sh", "-c", "npm start"])


class TestContainerManager(unittest.IsolatedAsyncioTestCase):
    async def test_available_true_when_info_ok(self):
        mgr = cs.ContainerManager()
        mgr._docker = AsyncMock(return_value=(0, "27.0.0", ""))
        self.assertTrue(await mgr.available())

    async def test_available_false_when_daemon_down(self):
        mgr = cs.ContainerManager()
        mgr._docker = AsyncMock(return_value=(1, "", "Cannot connect"))
        self.assertFalse(await mgr.available())

    async def test_available_false_when_rc0_but_empty(self):
        # `docker info --format` exits 0 even with the daemon down (empty render);
        # rc==0 alone must NOT count as healthy, else 'auto' never falls back.
        mgr = cs.ContainerManager()
        mgr._docker = AsyncMock(return_value=(0, "\n", "Cannot connect to the Docker daemon"))
        self.assertFalse(await mgr.available())

    async def test_ensure_starts_when_not_running(self):
        mgr = cs.ContainerManager()
        calls = []

        async def fake_docker(argv, timeout=None):
            calls.append(argv)
            if argv[1] == "inspect":
                return (1, "", "no such container")   # not running
            if argv[1] == "run":
                return (0, "containerid", "")
            return (0, "", "")

        mgr._docker = fake_docker
        await mgr.ensure(7, "/ws/group_7")
        self.assertTrue(any(a[1] == "run" for a in calls))
        self.assertIn(7, mgr._last_used)          # activity recorded
        await mgr.close()                          # cancel the reaper it started

    async def test_ensure_skips_when_already_running(self):
        mgr = cs.ContainerManager()
        calls = []

        async def fake_docker(argv, timeout=None):
            calls.append(argv)
            if argv[1] == "inspect":
                return (0, "true\n", "")              # already running
            return (0, "", "")

        mgr._docker = fake_docker
        await mgr.ensure(7, "/ws/group_7")
        self.assertFalse(any(a[1] == "run" for a in calls))
        await mgr.close()

    async def test_exec_foreground_timeout_maps_124(self):
        mgr = cs.ContainerManager()
        mgr._docker = AsyncMock(return_value=(124, "", ""))
        rc, out, err, timed_out = await mgr.exec_foreground(
            1, cmd="sleep 99", cwd="/ws", env={}, timeout=1
        )
        self.assertTrue(timed_out)
        self.assertIsNone(rc)

    async def test_exec_foreground_ok(self):
        mgr = cs.ContainerManager()
        mgr._docker = AsyncMock(return_value=(0, "hello\n", ""))
        rc, out, err, timed_out = await mgr.exec_foreground(
            1, cmd="echo hello", cwd="/ws", env={}, timeout=5
        )
        self.assertEqual((rc, out, timed_out), (0, "hello", False))

    async def test_docker_timeout_logs_when_kill_fails(self):
        mgr = cs.ContainerManager()

        class _Proc:
            def __init__(self):
                self.returncode = None

            async def communicate(self):
                raise asyncio.TimeoutError()

            def kill(self):
                raise RuntimeError("kill failed")

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            return _Proc()

        async def fake_wait_for(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError()

        with patch("executors.plugins.container_sandbox.asyncio.create_subprocess_exec", new=fake_create_subprocess_exec), \
             patch("executors.plugins.container_sandbox.asyncio.wait_for", new=fake_wait_for), \
             self.assertLogs("executors.plugins.container_sandbox", level="ERROR") as logs:
            rc, out, err = await mgr._docker(["docker", "ps"], timeout=1)

        self.assertEqual((rc, out, err), (124, "", "docker call timed out"))
        self.assertTrue(any("failed to kill timed-out process" in line for line in logs.output))


class TestReaper(unittest.IsolatedAsyncioTestCase):
    async def test_idle_groups_pure(self):
        mgr = cs.ContainerManager()
        now = 10_000.0
        mgr._last_used = {1: now - 9999, 2: now - 10}     # 1 idle, 2 fresh
        self.assertEqual(mgr.idle_groups(now, idle_timeout=100), [1])

    async def test_reap_once_stops_idle_keeps_fresh(self):
        from unittest.mock import AsyncMock
        mgr = cs.ContainerManager()
        mgr.stop = AsyncMock()
        now = time.monotonic()
        mgr._last_used = {1: now - 9999, 2: now}          # group 1 idle (>1800s default)
        reaped = await mgr.reap_once()
        self.assertEqual(reaped, [1])
        mgr.stop.assert_awaited_once_with(1)
        self.assertNotIn(1, mgr._last_used)               # forgotten after reap
        self.assertIn(2, mgr._last_used)                  # fresh kept

    async def test_reap_once_noop_when_all_fresh(self):
        from unittest.mock import AsyncMock
        mgr = cs.ContainerManager()
        mgr.stop = AsyncMock()
        mgr._last_used = {1: time.monotonic(), 2: time.monotonic()}
        self.assertEqual(await mgr.reap_once(), [])
        mgr.stop.assert_not_awaited()

    async def test_touch_updates_last_used(self):
        mgr = cs.ContainerManager()
        mgr._touch(5)
        self.assertIn(5, mgr._last_used)

    async def test_start_reaper_idempotent(self):
        mgr = cs.ContainerManager()
        mgr.start_reaper()
        first = mgr._reaper_task
        mgr.start_reaper()
        self.assertIs(mgr._reaper_task, first)            # not replaced
        await mgr.close()
        self.assertIsNone(mgr._reaper_task)


class _FakeManager:
    def __init__(self):
        self.ensured = None
        self.fg = None

    async def available(self):
        return True

    async def ensure(self, group_id, workspace_dir):
        self.ensured = (group_id, str(workspace_dir))

    async def exec_foreground(self, group_id, *, cmd, cwd, env, timeout):
        self.fg = {"group_id": group_id, "cmd": cmd, "cwd": cwd, "timeout": timeout}
        return (0, "out", "", False)

    async def exec_background(self, group_id, *, cmd, cwd, env):
        return f"nuke-sbx-{group_id} (detached)"


class TestContainerShellBackendDelegation(unittest.IsolatedAsyncioTestCase):
    def _req(self, group_id=4):
        return ShellExecRequest(
            cmd="echo hi", work_dir=Path("/ws/group_4/workspace"), env={"PATH": "/usr/bin"},
            group_id=group_id, bot_id=1, mem_limit_bytes=512, timeout_s=30,
        )

    async def test_run_foreground_maps_result(self):
        fake = _FakeManager()
        backend = ContainerShellBackend(manager=fake)
        result = await backend.run_foreground(self._req())
        self.assertEqual((result.exit_code, result.stdout, result.timed_out), (0, "out", False))
        self.assertEqual(fake.fg["cmd"], "echo hi")           # no ulimit wrap (cgroup does it)
        self.assertEqual(fake.fg["cwd"], "/ws/group_4/workspace")

    async def test_ensure_ready_requires_group_id(self):
        backend = ContainerShellBackend(manager=_FakeManager())
        with self.assertRaises(RuntimeError):
            await backend.ensure_ready(None)

    async def test_healthy_delegates(self):
        backend = ContainerShellBackend(manager=_FakeManager())
        self.assertTrue(await backend.healthy())

    async def test_background_returns_handle(self):
        backend = ContainerShellBackend(manager=_FakeManager())
        h = await backend.start_background(self._req())
        self.assertIn("nuke-sbx-4", h.identifier)


if __name__ == "__main__":
    unittest.main()
