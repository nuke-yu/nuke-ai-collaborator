import unittest
from unittest.mock import AsyncMock, MagicMock
import sys
import os
import asyncio

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ws_manager
from ws_manager import WSManager

class TestWSManager(unittest.IsolatedAsyncioTestCase):

    async def test_broadcast_dead_connection_presence(self):
        """Test DFT-009: dead connection results in disconnect and presence broadcast."""
        manager = WSManager()
        
        # Create two mock WebSockets
        ws1 = MagicMock()
        # Mock connection accept for connect method
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock(side_effect=Exception("Connection dead!")) # Will fail
        
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock() # Will succeed
        
        # Connect both WebSockets as member 10 and 20 respectively in group 1
        await manager.connect(ws1, group_id=1, member_id=10)
        await manager.connect(ws2, group_id=1, member_id=20)
        
        # Ensure we have 2 connections
        self.assertEqual(len(manager.connections[1]), 2)
        
        # Broadcast a message. This should encounter Exception in ws1,
        # add it to dead list, disconnect member 10 (since they went offline),
        # and broadcast a presence offline message for member 10.
        await manager.broadcast(group_id=1, message={"type": "hello"})
        
        # ws1 should be disconnected, leaving only ws2
        self.assertEqual(len(manager.connections[1]), 1)
        self.assertEqual(manager.connections[1][0][0], ws2)
        
        # Check ws2 received calls:
        # It should receive the first message {"type": "hello"}
        # And then the presence broadcast {"type": "presence", "member_id": 10, "online": False}
        self.assertEqual(ws2.send_json.call_count, 2)
        ws2.send_json.assert_any_call({"type": "hello"})
        ws2.send_json.assert_any_call({"type": "presence", "member_id": 10, "online": False})

    async def test_broadcast_concurrent_modification(self):
        """Test DFT-015: broadcast doesn't raise RuntimeError if connection list is modified during iteration."""
        manager = WSManager()
        
        ws1 = MagicMock()
        ws1.accept = AsyncMock()
        
        # ws1 disconnects itself during send_json call to mutate self.connections list during iteration
        async def mock_send_json(message):
            manager.disconnect(ws1, group_id=1)
            
        ws1.send_json = AsyncMock(side_effect=mock_send_json)
        
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()
        
        await manager.connect(ws1, group_id=1, member_id=10)
        await manager.connect(ws2, group_id=1, member_id=20)
        
        # Calling broadcast should run successfully without raising:
        # RuntimeError: list size changed during iteration.
        try:
            await manager.broadcast(group_id=1, message={"type": "ping"})
        except RuntimeError as e:
            self.fail(f"broadcast raised RuntimeError: {e}")
            
        # ws1 should be disconnected
        self.assertEqual(len(manager.connections[1]), 1)
        self.assertEqual(manager.connections[1][0][0], ws2)

class _FakeWS:
    """Minimal websocket double: send_json can hang forever (half-open client)."""

    def __init__(self, hang=False):
        self.hang = hang
        self.sent = []
        self._never = asyncio.Event()  # never set → send hangs

    async def accept(self):
        pass

    async def send_json(self, message):
        if self.hang:
            await self._never.wait()
        self.sent.append(message)


class TestBroadcastSendTimeout(unittest.IsolatedAsyncioTestCase):
    """DFT-030: a hanging send_json must not stall the shared broadcast loop."""

    def setUp(self):
        self._orig = ws_manager._SEND_TIMEOUT
        ws_manager._SEND_TIMEOUT = 0.05

    def tearDown(self):
        ws_manager._SEND_TIMEOUT = self._orig

    async def test_slow_client_times_out_and_is_removed(self):
        mgr = WSManager()
        fast = _FakeWS()
        slow = _FakeWS(hang=True)
        await mgr.connect(fast, group_id=1, member_id=10)
        await mgr.connect(slow, group_id=1, member_id=20)

        await asyncio.wait_for(mgr.broadcast(1, {"type": "x", "n": 1}), timeout=2)

        self.assertIn({"type": "x", "n": 1}, fast.sent)
        remaining = [ws for ws, _ in mgr.connections.get(1, [])]
        self.assertIn(fast, remaining)
        self.assertNotIn(slow, remaining)

    async def test_broadcast_returns_promptly_despite_hang(self):
        mgr = WSManager()
        slow = _FakeWS(hang=True)
        await mgr.connect(slow, group_id=2, member_id=30)

        loop = asyncio.get_event_loop()
        start = loop.time()
        await asyncio.wait_for(mgr.broadcast(2, {"type": "y"}), timeout=2)
        elapsed = loop.time() - start
        self.assertLess(elapsed, 1.0)  # bounded by _SEND_TIMEOUT, not forever


if __name__ == "__main__":
    unittest.main()
