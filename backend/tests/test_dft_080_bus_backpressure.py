import asyncio
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bus.engine import EventBus
from bus.events import Message

class TestDFT080BusBackpressure(unittest.IsolatedAsyncioTestCase):
    async def test_slow_subscriber_does_not_block_others(self):
        bus = EventBus()
        
        # 1. Create a very small queue for a slow subscriber
        sub_slow = bus.subscribe(Message, maxsize=2)
        
        # 2. Create a normal queue for a fast subscriber
        sub_fast = bus.subscribe(Message, maxsize=100)
        
        # 3. Publish 10 messages
        # Fast subscriber should get them all.
        # Slow subscriber should only get the first 2, and others dropped.
        for i in range(10):
            await bus.publish(Message(
                group_id=1, id=i, member_id=1, sender_name="bot",
                sender_type="bot", content=f"msg {i}", created_at="now"
            ))
            
        # 4. Verify fast subscriber received all 10
        fast_count = 0
        while not sub_fast._queue.empty():
            await sub_fast._queue.get()
            fast_count += 1
        self.assertEqual(fast_count, 10)
        
        # 5. Verify slow subscriber only has 2
        slow_count = 0
        while not sub_slow._queue.empty():
            await sub_slow._queue.get()
            slow_count += 1
        self.assertEqual(slow_count, 2)
        
    async def test_wildcard_backpressure(self):
        bus = EventBus()
        sub_all = bus.subscribe_all(maxsize=1)
        
        await bus.broadcast(1, {"type": "test", "data": 1})
        await bus.broadcast(1, {"type": "test", "data": 2}) # Should be dropped
        
        count = 0
        while not sub_all._queue.empty():
            await sub_all._queue.get()
            count += 1
        self.assertEqual(count, 1)

if __name__ == "__main__":
    unittest.main()
