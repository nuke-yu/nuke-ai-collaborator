import unittest
from unittest.mock import AsyncMock, MagicMock
import sys
import os
import asyncio

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

if __name__ == "__main__":
    unittest.main()
