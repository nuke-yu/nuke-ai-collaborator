"""CELL-18: Group reassignment handoff tests."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.supervisor import Supervisor
from runtime import ipc
from runtime.lifecycle import manager as lifecycle_mgr

class TestCell18Handoff(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_handoff_protocol(self):
        sup = Supervisor("dummy_addr")
        
        # Mock DB
        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass
            
        # Mock workers
        w1_writer = AsyncMock()
        w2_writer = AsyncMock()
        
        sup._workers["w1"] = w1_writer
        sup._workers["w2"] = w2_writer
        sup._routing_cache[77] = ("w1", 9999999999.0)
        
        with patch("db.global_db", return_value=MockDB()):
            with patch("runtime.ipc.send_msg", new_callable=AsyncMock) as mock_send:
                
                # We need to simulate the worker sending LEASE_RELEASED back.
                # We'll create a task that waits a tiny bit then calls _on_upstream.
                async def simulate_worker_ack():
                    await asyncio.sleep(0.05)
                    await sup._on_upstream(ipc.protocol.envelope(
                        ipc.protocol.LEASE_RELEASED, group_id=77, worker_id="w1"
                    ))
                
                asyncio.create_task(simulate_worker_ack())
                
                # Start handoff
                await sup.reassign_group(77, "w2")
                
                # Verify RELEASE_LEASE was sent to w1
                mock_send.assert_called_once_with(w1_writer, ipc.protocol.envelope(
                    ipc.protocol.RELEASE_LEASE, group_id=77
                ))
                
                # Verify routing cache was updated AFTER ack
                self.assertEqual(sup._routing_cache[77][0], "w2")

    async def test_handoff_timeout(self):
        sup = Supervisor("dummy_addr")
        
        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass
            
        w1_writer = AsyncMock()
        sup._workers["w1"] = w1_writer
        sup._routing_cache[77] = ("w1", 9999999999.0)
        
        with patch("db.global_db", return_value=MockDB()):
            with patch("runtime.ipc.send_msg", new_callable=AsyncMock):
                with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                    # No ACK sent, wait_for times out
                    await sup.reassign_group(77, "w2")
                    
                    # Routing should STILL be updated to avoid being stuck forever
                    self.assertEqual(sup._routing_cache[77][0], "w2")

    async def test_handoff_send_failure_skips_ack_wait_and_updates_route(self):
        sup = Supervisor("dummy_addr")

        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass

        sup._workers["w1"] = AsyncMock()
        sup._routing_cache[77] = ("w1", 9999999999.0)

        with patch("db.global_db", return_value=MockDB()):
            with patch.object(sup, "send_to_worker_id", new=AsyncMock(return_value=False)) as mock_send:
                with patch("asyncio.wait_for", new=AsyncMock()) as mock_wait:
                    await sup.reassign_group(77, "w2")

        mock_send.assert_awaited_once_with("w1", ipc.protocol.envelope(
            ipc.protocol.RELEASE_LEASE, group_id=77
        ))
        mock_wait.assert_not_awaited()
        self.assertEqual(sup._routing_cache[77][0], "w2")
        self.assertEqual(sup._pending_handoffs, {})

    async def test_reassign_group_cancels_replaced_pending_handoff(self):
        sup = Supervisor("dummy_addr")

        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass

        sup._workers["w1"] = AsyncMock()
        sup._routing_cache[77] = ("w1", 9999999999.0)
        prev = asyncio.get_running_loop().create_future()
        sup._pending_handoffs[77] = ("w1", prev)

        with patch("db.global_db", return_value=MockDB()):
            with patch.object(sup, "send_to_worker_id", new=AsyncMock(return_value=False)):
                await sup.reassign_group(77, "w2")

        self.assertTrue(prev.cancelled())
        self.assertEqual(sup._routing_cache[77][0], "w2")
        self.assertEqual(sup._pending_handoffs, {})

    async def test_direct_reassign_clears_stale_pending_handoff(self):
        sup = Supervisor("dummy_addr")

        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass

        fut = asyncio.get_running_loop().create_future()
        sup._pending_handoffs[77] = ("w1", fut)

        with patch("db.global_db", return_value=MockDB()):
            await sup.reassign_group(77, "w2")

        self.assertTrue(fut.cancelled())
        self.assertEqual(sup._routing_cache[77][0], "w2")
        self.assertEqual(sup._pending_handoffs, {})

    async def test_drop_worker_state_resolves_pending_handoff_for_stale_group(self):
        sup = Supervisor("dummy_addr")
        writer = AsyncMock()
        sup._workers["w1"] = writer
        sup._routing_cache[77] = ("w1", 9999999999.0)
        fut = asyncio.get_running_loop().create_future()
        sup._pending_handoffs[77] = ("w1", fut)

        sup._drop_worker_state("w1", writer=writer)

        self.assertTrue(fut.done())
        self.assertFalse(fut.result())
        self.assertNotIn("w1", sup._workers)
        self.assertNotIn(77, sup._routing_cache)
        self.assertNotIn(77, sup._pending_handoffs)

    async def test_drop_worker_state_resolves_pending_handoffs_without_route_cache(self):
        sup = Supervisor("dummy_addr")
        writer = AsyncMock()
        sup._workers["w1"] = writer
        fut = asyncio.get_running_loop().create_future()
        sup._pending_handoffs[77] = ("w1", fut)

        sup._drop_worker_state("w1", writer=writer)

        self.assertTrue(fut.done())
        self.assertFalse(fut.result())
        self.assertNotIn(77, sup._pending_handoffs)

    async def test_handoff_disconnect_result_does_not_log_success(self):
        sup = Supervisor("dummy_addr")

        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass

        sup._workers["w1"] = AsyncMock()
        sup._routing_cache[77] = ("w1", 9999999999.0)

        with patch("db.global_db", return_value=MockDB()):
            with patch.object(sup, "send_to_worker_id", new=AsyncMock(return_value=True)):
                with patch("asyncio.wait_for", new=AsyncMock(return_value=False)):
                    with patch("runtime.supervisor.log.warning") as mock_warning, \
                         patch("runtime.supervisor.log.info") as mock_info:
                        await sup.reassign_group(77, "w2")

        mock_warning.assert_any_call(
            "supervisor: worker %s disappeared before confirming release of group %d",
            "w1", 77,
        )
        self.assertFalse(any(
            call.args == ("supervisor: handoff of group %d complete", 77)
            for call in mock_info.call_args_list
        ))

    async def test_handoff_returns_early_when_old_worker_disconnects_mid_wait(self):
        sup = Supervisor("dummy_addr")

        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass

        writer = AsyncMock()
        sup._workers["w1"] = writer
        sup._routing_cache[77] = ("w1", 9999999999.0)

        with patch("db.global_db", return_value=MockDB()):
            with patch.object(sup, "send_to_worker_id", new=AsyncMock(return_value=True)):
                async def disconnect_soon():
                    await asyncio.sleep(0.01)
                    sup._drop_worker_state("w1", writer=writer)

                asyncio.create_task(disconnect_soon())
                await asyncio.wait_for(sup.reassign_group(77, "w2"), timeout=1)

        self.assertEqual(sup._routing_cache[77][0], "w2")
        self.assertEqual(sup._pending_handoffs, {})

    async def test_late_or_duplicate_release_ack_is_ignored_after_cleanup(self):
        sup = Supervisor("dummy_addr")
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(True)
        sup._pending_handoffs[77] = ("w1", fut)
        await sup._on_upstream(ipc.protocol.envelope(
            ipc.protocol.LEASE_RELEASED, group_id=77, worker_id="w1"
        ))
        self.assertTrue(fut.done())
        self.assertTrue(fut.result())

        sup._pending_handoffs.pop(77, None)
        await sup._on_upstream(ipc.protocol.envelope(
            ipc.protocol.LEASE_RELEASED, group_id=77, worker_id="w1"
        ))
        self.assertEqual(sup._pending_handoffs, {})

    async def test_release_ack_from_stale_worker_does_not_complete_new_handoff(self):
        sup = Supervisor("dummy_addr")
        fut = asyncio.get_running_loop().create_future()
        sup._pending_handoffs[77] = ("w2", fut)

        await sup._on_upstream(ipc.protocol.envelope(
            ipc.protocol.LEASE_RELEASED, group_id=77, worker_id="w1"
        ))

        self.assertFalse(fut.done())
        self.assertEqual(sup._pending_handoffs[77][0], "w2")

    async def test_stale_reassign_does_not_clobber_newer_handoff_route(self):
        sup = Supervisor("dummy_addr")

        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass

        sup._workers["w1"] = AsyncMock()
        sup._routing_cache[77] = ("w1", 9999999999.0)
        first_send_started = asyncio.Event()
        release_first_send = asyncio.Event()
        send_calls = 0

        async def fake_send(worker_id, msg):
            nonlocal send_calls
            send_calls += 1
            if send_calls == 1:
                first_send_started.set()
                await release_first_send.wait()
                return True
            return False

        with patch("db.global_db", return_value=MockDB()):
            with patch.object(sup, "send_to_worker_id", new=fake_send):
                first = asyncio.create_task(sup.reassign_group(77, "w2"))
                await first_send_started.wait()
                await sup.reassign_group(77, "w3")
                release_first_send.set()
                await first

        self.assertEqual(sup._routing_cache[77][0], "w3")
        self.assertEqual(sup._pending_handoffs, {})

    async def test_timeout_clears_pending_handoff_before_late_ack(self):
        sup = Supervisor("dummy_addr")

        class MockDB:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, *args): pass
            async def commit(self): pass

        sup._workers["w1"] = AsyncMock()
        sup._routing_cache[77] = ("w1", 9999999999.0)

        with patch("db.global_db", return_value=MockDB()):
            with patch.object(sup, "send_to_worker_id", new=AsyncMock(return_value=True)):
                with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                    await sup.reassign_group(77, "w2")

        self.assertEqual(sup._pending_handoffs, {})
        self.assertEqual(sup._routing_cache[77][0], "w2")

        await sup._on_upstream(ipc.protocol.envelope(
            ipc.protocol.LEASE_RELEASED, group_id=77, worker_id="w1"
        ))
        self.assertEqual(sup._pending_handoffs, {})
        self.assertEqual(sup._routing_cache[77][0], "w2")

    async def test_eviction_persistence_barrier(self):
        from runtime.lifecycle import LifecycleManager
        mgr = LifecycleManager()
        
        mock_orch = AsyncMock()
        mock_orch.serialize = unittest.mock.MagicMock(return_value={"some_state": 123})
        
        with patch("core.workflow._group_orch", {99: "mock_orch_id"}), \
             patch("core.orchestration.registry.get", return_value=mock_orch), \
             patch("core.workflow_store.save_state", new_callable=AsyncMock) as mock_save_state, \
             patch("core.bg.abort_group") as mock_abort, \
             patch("db.aclose_writer", new_callable=AsyncMock), \
             patch("workspace.clear_group_locks"):
             
            await mgr._do_evict(99)
            
            mock_orch.serialize.assert_called_once_with(99)
            mock_save_state.assert_called_once_with(99, "mock_orch_id", {"some_state": 123})
            mock_abort.assert_called_once_with(99)

if __name__ == "__main__":
    unittest.main()
