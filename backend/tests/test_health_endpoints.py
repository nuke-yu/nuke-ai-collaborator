import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from main import app
from runtime import supervisor as sup_mod


class TestHealthEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_supervisor = sup_mod.supervisor
        sup_mod.supervisor = None

    async def asyncTearDown(self):
        sup_mod.supervisor = self._orig_supervisor

    async def test_liveness_success(self):
        # We need a working DB connection for the liveness DB write.
        # The test runner's default test DB is already initialized.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/liveness")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    async def test_liveness_db_failure(self):
        # Mock global_db to raise an exception
        with patch("db.global_db") as mock_db:
            mock_db.side_effect = Exception("DB Disk Full")
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/health/liveness")
            self.assertEqual(resp.status_code, 500)
            self.assertIn("Database not writable", resp.json()["detail"])

    async def test_readiness_no_supervisor(self):
        sup_mod.supervisor = None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/readiness")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "Supervisor not initialized")

    async def test_readiness_no_workers_configured(self):
        mock_sup = MagicMock()
        mock_sup._num_workers = 0
        sup_mod.supervisor = mock_sup

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/readiness")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")

    async def test_readiness_missing_collector(self):
        mock_sup = MagicMock()
        mock_sup._num_workers = 1
        mock_sup._workers = {"w0": object()}  # w0 is connected, but not mcp-collector
        mock_sup._worker_stats_ts = {"w0": time.time()}
        sup_mod.supervisor = mock_sup

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/readiness")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "mcp-collector is not connected")

    async def test_readiness_missing_worker(self):
        mock_sup = MagicMock()
        mock_sup._num_workers = 1
        mock_sup._workers = {"mcp-collector": object()}  # missing w0
        mock_sup._worker_stats_ts = {}
        sup_mod.supervisor = mock_sup

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/readiness")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "Worker w0 is not connected")

    async def test_readiness_heartbeat_not_received_yet(self):
        mock_sup = MagicMock()
        mock_sup._num_workers = 1
        mock_sup._workers = {"w0": object(), "mcp-collector": object()}
        mock_sup._worker_stats_ts = {}  # w0 has not sent a heartbeat yet
        sup_mod.supervisor = mock_sup

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/readiness")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "Worker w0 heartbeat not yet received")

    async def test_readiness_heartbeat_stale(self):
        mock_sup = MagicMock()
        mock_sup._num_workers = 1
        mock_sup._workers = {"w0": object(), "mcp-collector": object()}
        mock_sup._worker_stats_ts = {"w0": time.time() - 100}  # 100 seconds ago
        sup_mod.supervisor = mock_sup

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/readiness")
        self.assertEqual(resp.status_code, 503)
        self.assertTrue(resp.json()["detail"].startswith("Worker w0 heartbeat is too old"))

    async def test_readiness_success(self):
        mock_sup = MagicMock()
        mock_sup._num_workers = 1
        mock_sup._workers = {"w0": object(), "mcp-collector": object()}
        mock_sup._worker_stats_ts = {"w0": time.time() - 5}  # fresh heartbeat
        sup_mod.supervisor = mock_sup

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/readiness")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")
        self.assertIn("w0", resp.json()["connected_workers"])
        self.assertIn("mcp-collector", resp.json()["connected_workers"])
