"""DFT-057: /api/system/status 暴露单机运行指标（活跃任务 / 连接 / 待审批队列）。"""
import asyncio
import unittest

import httpx

from core import bg
import permissions
from permissions import engine as perm_engine
from permissions.models import _PendingRequest
from ws_manager import manager


class TestBgStats(unittest.IsolatedAsyncioTestCase):
    async def test_active_tasks_and_groups_counted(self):
        before = bg.stats()["active_tasks"]
        release = asyncio.Event()

        async def _hold():
            await release.wait()

        bg.spawn_group(99001, _hold())
        try:
            s = bg.stats()
            self.assertEqual(s["active_tasks"], before + 1)
            self.assertIn(99001, s["tasks_by_group"])
            self.assertEqual(s["tasks_by_group"][99001], 1)
            self.assertGreaterEqual(s["groups_with_active_tasks"], 1)
        finally:
            release.set()
            await asyncio.sleep(0)  # let the task finish + done-callbacks run

    async def test_finished_task_drops_out_of_stats(self):
        release = asyncio.Event()

        async def _hold():
            await release.wait()

        task = bg.spawn_group(99002, _hold())
        release.set()
        await task
        await asyncio.sleep(0)
        self.assertNotIn(99002, bg.stats()["tasks_by_group"])


class TestWsStats(unittest.TestCase):
    def test_connections_counted(self):
        gid = 99003
        manager.connections.setdefault(gid, []).append(("ws-sentinel", 7))
        try:
            s = manager.stats()
            self.assertEqual(s["connections_by_group"][gid], 1)
            self.assertGreaterEqual(s["total_connections"], 1)
            self.assertGreaterEqual(s["groups_online"], 1)
        finally:
            manager.connections.pop(gid, None)


class TestPermissionStats(unittest.TestCase):
    def test_pending_and_once_counted(self):
        before = permissions.pending_stats()
        rid = "sentinel-req-057"
        perm_engine._pending[rid] = _PendingRequest(
            future=None, bot_id=1, group_id=99004, tool_name="run_shell", arguments={},
        )
        perm_engine._once_grants[(1, 99004)] = [("run_shell", "deadbeef")]
        try:
            s = permissions.pending_stats()
            self.assertEqual(s["pending_requests"], before["pending_requests"] + 1)
            self.assertGreaterEqual(s["once_grants"], 1)
        finally:
            perm_engine._pending.pop(rid, None)
            perm_engine._once_grants.pop((1, 99004), None)


class TestSystemStatusRoute(unittest.IsolatedAsyncioTestCase):
    async def test_route_aggregates_all_sections(self):
        from main import app
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test", "is_operator": True}
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/system/status")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertIn("supervisor", body)
            self.assertIn("websockets", body)
            self.assertIn("permissions", body)
            self.assertIn("active_tasks", body["tasks"])
            self.assertIn("total_connections", body["websockets"])
            self.assertIn("pending_requests", body["permissions"])
        finally:
            app.dependency_overrides.pop(_auth.get_current_user, None)


if __name__ == "__main__":
    unittest.main()
