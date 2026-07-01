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
                        ipc.protocol.LEASE_RELEASED, group_id=77
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
