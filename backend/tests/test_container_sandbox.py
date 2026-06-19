"""Tests for the per-group container sandbox (Phase 2 substrate).

No live Docker daemon needed: the pure argv builders are tested directly, and
the single real-docker seam (ContainerManager._docker) is mocked. Real
end-to-end against a daemon is a separate manual step.
"""
import os
import sys
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

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
