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

if __name__ == "__main__":
    unittest.main()
