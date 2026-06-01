"""CELL-21: Supervisor fanout resilience tests."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.supervisor import Supervisor

class FastClient:
    def __init__(self):
        self.sent = []
    async def send(self, payload):
        self.sent.append(payload)

class FailingClient:
    async def send(self, payload):
        raise ConnectionError("Connection lost")

class TimeoutClient:
    async def send(self, payload):
        raise asyncio.TimeoutError()

class TestCell21Fanout(unittest.IsolatedAsyncioTestCase):
    @patch('runtime.supervisor.log')
    async def test_fanout_timeout_logic(self, mock_log):
        sup = Supervisor("dummy_addr")
        
        fast = FastClient()
        failing = FailingClient()
        timeout_client = TimeoutClient()
        
        sup.register_browser(1, fast)
        sup.register_browser(1, failing)
        sup.register_browser(1, timeout_client)
        
        # Real _fanout uses wait_for which will catch the TimeoutError
        await sup._fanout(1, {"msg": "hello"})
        
        # Fast client should have received it
        self.assertEqual(len(fast.sent), 1)
        
        # Failing and Timeout clients should be evicted
        self.assertNotIn(failing, sup._browsers[1])
        self.assertNotIn(timeout_client, sup._browsers[1])
        self.assertIn(fast, sup._browsers[1])
        
        # Warning should be logged twice
        self.assertEqual(mock_log.warning.call_count, 2)

if __name__ == "__main__":
    unittest.main()
