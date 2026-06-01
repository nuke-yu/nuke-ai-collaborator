"""CELL-20: Aggregated system status monitoring."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.supervisor import Supervisor
from runtime import ipc
import runtime.tracing

class TestCell20Stats(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_aggregates_stats(self):
        sup = Supervisor("dummy_addr")
        
        # Simulate receiving a STATS_REPORT from w0
        frame = ipc.protocol.envelope(
            ipc.protocol.STATS_REPORT,
            group_id=0,
            payload={
                "worker_id": "w0",
                "bg": {"active_tasks": 5},
                "lifecycle": {"active_groups_count": 2}
            }
        )
        
        # In a real environment, _on_upstream is called
        await sup._on_upstream(frame)
        
        # Now check the aggregated stats
        stats = sup.get_stats()
        
        self.assertIn("workers", stats)
        self.assertIn("w0", stats["workers"])
        self.assertEqual(stats["workers"]["w0"]["bg"]["active_tasks"], 5)
        self.assertEqual(stats["workers"]["w0"]["lifecycle"]["active_groups_count"], 2)

if __name__ == "__main__":
    unittest.main()
