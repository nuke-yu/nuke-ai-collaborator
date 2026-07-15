"""tests/test_abort_ack_protocol.py — P0-2: Abort/Retry IPC ACK protocol tests.

Tests the ABORT_REQUEST/ABORT_ACK protocol:
  - Supervisor sends ABORT_REQUEST, waits for ABORT_ACK
  - Worker cancels tasks, waits for cleanup, sends ABORT_ACK
  - Timeout/disconnect fails closed (no retry, no false success)
"""
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from runtime.supervisor import Supervisor
from runtime import ipc


class TestAbortAckProtocol(unittest.IsolatedAsyncioTestCase):
    """Test the ABORT_REQUEST/ABORT_ACK protocol."""

    async def test_request_abort_success(self):
        """Successful abort: Supervisor sends request, Worker responds with ACK."""
        sup = Supervisor("dummy_addr", num_workers=1)
        sup._workers = {"w0": MagicMock()}

        # Mock send_to_worker to succeed
        sup.send_to_worker = AsyncMock()

        # Simulate Worker ACK arriving after 0.1s
        async def simulate_ack():
            await asyncio.sleep(0.1)
            request_id = list(sup._abort_requests.keys())[0]
            fut = sup._abort_requests[request_id]
            fut.set_result({
                "type": "abort_ack",
                "group_id": 10,
                "request_id": request_id,
                "task_id": "task_1",
                "mode": "abort",
                "cancelled_count": 2,
                "cleanup_status": "success",
                "workspace_action": "discard",
                "workspace_cleaned": True,
                "error": None,
            })

        asyncio.create_task(simulate_ack())

        # Request abort
        ack = await sup.request_abort(
            group_id=10, task_id="task_1", mode="abort", timeout=5.0
        )

        # Verify ACK received
        self.assertEqual(ack["cancelled_count"], 2)
        self.assertEqual(ack["cleanup_status"], "success")
        self.assertIsNone(ack["error"])

    async def test_request_abort_timeout_fails_closed(self):
        """Timeout: Worker doesn't respond, raises TimeoutError (fail closed)."""
        sup = Supervisor("dummy_addr", num_workers=1)
        sup._workers = {"w0": MagicMock()}
        sup.send_to_worker = AsyncMock()

        # Request abort with short timeout
        with self.assertRaises(TimeoutError) as ctx:
            await sup.request_abort(
                group_id=10, task_id="task_1", mode="abort", timeout=0.1
            )

        self.assertIn("did not respond", str(ctx.exception))
        # Verify request was cleaned up
        self.assertEqual(len(sup._abort_requests), 0)

    async def test_request_abort_send_failure_fails_closed(self):
        """Send failure: Supervisor can't reach Worker, raises RuntimeError."""
        sup = Supervisor("dummy_addr", num_workers=1)
        sup._workers = {"w0": MagicMock()}

        # Mock send_to_worker to fail
        sup.send_to_worker = AsyncMock(side_effect=RuntimeError("Worker disconnected"))

        with self.assertRaises(RuntimeError) as ctx:
            await sup.request_abort(
                group_id=10, task_id="task_1", mode="abort", timeout=5.0
            )
        self.assertIn("Failed to send ABORT_REQUEST", str(ctx.exception))
        self.assertEqual(len(sup._abort_requests), 0)

    async def test_request_abort_rejects_partial_or_mismatched_ack(self):
        sup = Supervisor("dummy_addr", num_workers=1)
        sup.send_to_worker = AsyncMock()

        async def simulate_partial_ack():
            while not sup._abort_requests:
                await asyncio.sleep(0)
            request_id, fut = next(iter(sup._abort_requests.items()))
            fut.set_result({
                "type": "abort_ack",
                "group_id": 10,
                "request_id": request_id,
                "task_id": "another_task",
                "mode": "abort",
                "cleanup_status": "partial",
                "workspace_action": "discard",
                "workspace_cleaned": False,
            })

        asyncio.create_task(simulate_partial_ack())
        with self.assertRaisesRegex(RuntimeError, "invalid ABORT_ACK"):
            await sup.request_abort(10, task_id="task_1", timeout=1)

        self.assertEqual(len(sup._abort_requests), 0)

    async def test_abort_ack_received_by_supervisor(self):
        """Supervisor correctly processes ABORT_ACK from Worker."""
        sup = Supervisor("dummy_addr", num_workers=1)

        # Create a pending request
        request_id = "test-request-123"
        fut = asyncio.get_event_loop().create_future()
        sup._abort_requests[request_id] = fut

        # Simulate ABORT_ACK frame from Worker
        ack_frame = {
            "type": "abort_ack",
            "group_id": 10,
            "request_id": request_id,
            "cancelled_count": 3,
            "cleanup_status": "success",
            "error": None,
        }

        # Process the ACK
        await sup._on_upstream(ack_frame)

        # Verify Future was resolved
        self.assertTrue(fut.done())
        result = fut.result()
        self.assertEqual(result["cancelled_count"], 3)

        # Verify request was removed from tracking
        self.assertNotIn(request_id, sup._abort_requests)

    async def test_abort_ack_unknown_request_ignored(self):
        """ABORT_ACK for unknown request_id is logged and ignored."""
        sup = Supervisor("dummy_addr", num_workers=1)

        # No pending requests
        self.assertEqual(len(sup._abort_requests), 0)

        # Simulate ABORT_ACK for unknown request
        ack_frame = {
            "type": "abort_ack",
            "group_id": 10,
            "request_id": "unknown-request",
            "cancelled_count": 1,
            "cleanup_status": "success",
            "error": None,
        }

        # Should not raise
        await sup._on_upstream(ack_frame)

        # Still no pending requests
        self.assertEqual(len(sup._abort_requests), 0)


class TestWorkerAbortRequestHandling(unittest.IsolatedAsyncioTestCase):
    """Test Worker's handling of ABORT_REQUEST."""

    async def test_worker_sends_ack_after_cleanup(self):
        """Worker cancels tasks, waits for cleanup, sends ABORT_ACK."""
        from runtime.worker import Worker

        worker = Worker("w0", "dummy_addr")
        worker._writer = MagicMock()

        # Mock bg.abort_group to return cancelled count
        with patch("core.bg.abort_group", return_value=2), \
             patch("workspace.git_worktree.remove_worktree", new_callable=AsyncMock), \
             patch("workspace.layout.group_dir", return_value=Path("/tmp/nuke-abort-test")):
            # Mock ipc.send_msg to capture the ACK
            sent_messages = []
            async def capture_send(writer, msg):
                sent_messages.append(msg)
            with patch("runtime.ipc.send_msg", side_effect=capture_send):
                # Handle ABORT_REQUEST
                await worker._handle_abort_request(
                    gid=10,
                    request_id="test-req-456",
                    mode="abort",
                    task_id="task_1",
                    tid="trace-789",
                )

        # Verify ACK was sent
        self.assertEqual(len(sent_messages), 1)
        ack = sent_messages[0]
        self.assertEqual(ack["type"], "abort_ack")
        self.assertEqual(ack["request_id"], "test-req-456")
        self.assertEqual(ack["cancelled_count"], 2)
        self.assertEqual(ack["cleanup_status"], "success")
        self.assertEqual(ack["task_id"], "task_1")
        self.assertEqual(ack["workspace_action"], "discard")
        self.assertIs(ack["workspace_cleaned"], True)

    async def test_worker_timeout_is_failed_not_partial(self):
        from runtime.worker import Worker

        worker = Worker("w0", "dummy_addr")
        worker._writer = MagicMock()
        pending = asyncio.get_running_loop().create_future()
        sent_messages = []

        async def capture_send(_writer, msg):
            sent_messages.append(msg)

        with patch.dict("core.bg._group_tasks", {10: {pending}}, clear=True), \
             patch("core.bg.abort_group", return_value=1), \
             patch("runtime.worker.asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)), \
             patch("runtime.ipc.send_msg", side_effect=capture_send):
            await worker._handle_abort_request(
                gid=10,
                request_id="timeout-req",
                mode="abort",
                task_id="task_1",
                tid="trace-timeout",
            )

        self.assertEqual(sent_messages[0]["cleanup_status"], "failed")
        self.assertIs(sent_messages[0]["workspace_cleaned"], False)
        pending.cancel()

    async def test_worker_sends_ack_on_error(self):
        """Worker sends ABORT_ACK with error status if cleanup fails."""
        from runtime.worker import Worker

        worker = Worker("w0", "dummy_addr")
        worker._writer = MagicMock()

        # Mock bg.abort_group to raise exception
        with patch("core.bg.abort_group", side_effect=RuntimeError("Cleanup failed")):
            sent_messages = []
            async def capture_send(writer, msg):
                sent_messages.append(msg)
            with patch("runtime.ipc.send_msg", side_effect=capture_send):
                await worker._handle_abort_request(
                    gid=10,
                    request_id="test-req-789",
                    mode="abort",
                    task_id="task_1",
                    tid="trace-101",
                )

        # Verify ACK was sent with error status
        self.assertEqual(len(sent_messages), 1)
        ack = sent_messages[0]
        self.assertEqual(ack["type"], "abort_ack")
        self.assertEqual(ack["cleanup_status"], "failed")
        self.assertIn("Cleanup failed", ack["error"])


class TestOrchestratorAbortIntegration(unittest.IsolatedAsyncioTestCase):
    """Test orchestrator's use of abort ACK protocol."""

    async def asyncSetUp(self):
        """Set up isolated test database with migrations."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import db as _database
        import db.writer as _db_writer

        self._tmp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self._tmp.name).resolve()

        # Pin the DB path globals to an isolated temp DB and build its full schema
        self._db_file = str(self.workspace_root / "test_abort_ack.db")
        self._orig_db_paths = (_database.DB_PATH, _db_writer.DB_PATH)
        _database.DB_PATH = self._db_file
        _db_writer.DB_PATH = self._db_file
        await _database.init_db()

    async def asyncTearDown(self):
        """Clean up test database."""
        import db as _database
        import db.writer as _db_writer

        _database.DB_PATH, _db_writer.DB_PATH = self._orig_db_paths
        self._tmp.cleanup()

    async def _create_test_task(self, orch, task_id, group_id, bot_id, status="running", **kwargs):
        """Helper to create a test task in the database.

        Also creates the necessary group and bot if they don't exist.
        """
        import db

        # Create group if it doesn't exist
        async with db.write_connect() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO groups (id, name) VALUES (?, ?)",
                (group_id, f"Test Group {group_id}")
            )
            # Create bot if it doesn't exist
            await conn.execute(
                "INSERT OR IGNORE INTO members (id, group_id, name, type) VALUES (?, ?, ?, ?)",
                (bot_id, group_id, f"Test Bot {bot_id}", "bot")
            )
            await conn.commit()

        await orch._task_store.create_task(
            task_id=task_id,
            group_id=group_id,
            bot_id=bot_id,
            repo_url=kwargs.get("repo_url", "https://github.com/user/repo.git"),
            requirements=kwargs.get("requirements", "Test requirements"),
            base_branch=kwargs.get("base_branch", "main"),
            test_command=kwargs.get("test_command", "pytest"),
            model=kwargs.get("model", "deepseek-chat"),
            max_iterations=kwargs.get("max_iterations", 100),
        )
        if status != "created":
            await orch._task_store.update_status(task_id, status)

    async def test_retry_waits_for_ack_before_redispatch(self):
        """retry_task only re-dispatches after receiving successful ACK."""
        from plugins.agent_dashboard.orchestrator import TaskOrchestrator

        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)
        await self._create_test_task(orch, "task_1", 10, 5, status="stuck", requirements="Fix bug")

        # Mock _send_abort to return ACK
        mock_abort = AsyncMock(return_value={
            "cancelled_count": 1,
            "cleanup_status": "success",
            "error": None,
        })
        mock_dispatch = AsyncMock()

        with patch.object(orch, "_send_abort", mock_abort), \
             patch.object(orch, "_dispatch_agent", mock_dispatch):

            result = await orch.retry_task("task_1")

        # Verify abort was called with mode="retry"
        mock_abort.assert_called_once_with(10, "task_1", mode="retry")

        # Verify dispatch happened AFTER abort
        mock_dispatch.assert_called_once()

        # Verify status updated
        self.assertEqual(result["status"], "restarted")

    async def test_abort_waits_for_ack_before_terminal_state(self):
        """abort_task only writes terminal state after receiving successful ACK."""
        from plugins.agent_dashboard.orchestrator import TaskOrchestrator

        adapter = MagicMock()
        orch = TaskOrchestrator(adapter=adapter)
        await self._create_test_task(orch, "task_1", 10, 5, status="running")

        # Mock _send_abort to return ACK
        mock_abort = AsyncMock(return_value={
            "cancelled_count": 1,
            "cleanup_status": "success",
            "error": None,
        })

        with patch.object(orch, "_send_abort", mock_abort):
            result = await orch.abort_task("task_1")

        # Verify abort was called with mode="abort"
        mock_abort.assert_called_once_with(10, "task_1", mode="abort")

        # Verify terminal state written
        self.assertEqual(result["status"], "aborted")

    async def test_retry_fails_closed_on_abort_timeout(self):
        """retry_task raises TimeoutError if abort times out (fail closed)."""
        from plugins.agent_dashboard.orchestrator import TaskOrchestrator

        orch = TaskOrchestrator()
        await self._create_test_task(orch, "task_1", 10, 5, status="stuck", requirements="Fix bug")

        # Mock _send_abort to raise TimeoutError
        mock_abort = AsyncMock(side_effect=TimeoutError("Worker did not respond"))
        mock_dispatch = AsyncMock()

        with patch.object(orch, "_send_abort", mock_abort), \
             patch.object(orch, "_dispatch_agent", mock_dispatch):

            with self.assertRaises(TimeoutError):
                await orch.retry_task("task_1")

        # Verify dispatch was NOT called (fail closed)
        mock_dispatch.assert_not_called()

        # Verify status NOT updated (fetch from DB)
        task = await orch._task_store.get_task("task_1")
        self.assertEqual(task["status"], "stuck")

    async def test_retry_dispatch_failure_keeps_previous_status(self):
        from plugins.agent_dashboard.orchestrator import TaskOrchestrator

        orch = TaskOrchestrator()
        await self._create_test_task(orch, "task_1", 10, 5, status="stuck")

        with patch.object(orch, "_send_abort", new=AsyncMock()), \
             patch.object(
                 orch,
                 "_dispatch_agent",
                 new=AsyncMock(side_effect=RuntimeError("worker disconnected")),
             ):
            with self.assertRaisesRegex(RuntimeError, "worker disconnected"):
                await orch.retry_task("task_1")

        task = await orch._task_store.get_task("task_1")
        self.assertEqual(task["status"], "stuck")

    async def test_abort_fails_closed_on_cleanup_failure(self):
        """abort_task raises RuntimeError if Worker cleanup fails (fail closed)."""
        from plugins.agent_dashboard.orchestrator import TaskOrchestrator

        orch = TaskOrchestrator()
        await self._create_test_task(orch, "task_1", 10, 5, status="running")

        # Mock _send_abort to raise RuntimeError (cleanup failed)
        mock_abort = AsyncMock(side_effect=RuntimeError("Worker cleanup failed"))

        with patch.object(orch, "_send_abort", mock_abort):
            with self.assertRaises(RuntimeError) as ctx:
                await orch.abort_task("task_1")

        # Verify error message
        self.assertIn("cleanup failed", str(ctx.exception))

        # Verify status NOT updated (fetch from DB, fail closed)
        task = await orch._task_store.get_task("task_1")
        self.assertEqual(task["status"], "running")
