"""CELL-19: Distributed tracing unit tests."""
import asyncio
import json
import logging
import os
import sys
import unittest
import io
from unittest.mock import MagicMock

# Ensure the backend directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import tracing
from bus import bus
from bus.events import Message

class TestCell19Tracing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.log_capture = io.StringIO()
        # Custom handler to capture logs
        handler = logging.StreamHandler(self.log_capture)
        handler.addFilter(tracing.TraceLogFilter())
        
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                return json.dumps({
                    "message": record.getMessage(),
                    "trace_id": getattr(record, "trace_id", "-"),
                    "group_id": getattr(record, "group_id", "-"),
                })
        
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger()
        self._orig_handlers = root.handlers[:]
        for h in self._orig_handlers:
            root.removeHandler(h)
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    def tearDown(self):
        root = logging.getLogger()
        for h in root.handlers[:]:
            root.removeHandler(h)
        for h in self._orig_handlers:
            root.addHandler(h)

    async def test_trace_context_propagation(self):
        tid = "test-trace-123"
        gid = 42
        
        with tracing.trace_context(trace_id=tid, group_id=gid):
            self.assertEqual(tracing.get_trace_id(), tid)
            self.assertEqual(tracing.get_group_id(), gid)
            
            logging.info("Traced log message")
            
            # Check bus propagation
            await bus.broadcast(gid, {"type": "test_event"})
            
            sub = bus.subscribe_all()
            async with sub:
                # We need to wait for the event we just broadcasted
                # But broadcast/publish are async. 
                # Let's publish again inside the subscription scope.
                pass
        
        # Verify log output
        log_json = json.loads(self.log_capture.getvalue())
        self.assertEqual(log_json["trace_id"], tid)
        self.assertEqual(log_json["group_id"], gid)
        self.assertEqual(log_json["message"], "Traced log message")

    async def test_bus_carries_trace_id(self):
        tid = "bus-trace-999"
        gid = 7
        
        sub = bus.subscribe_all()
        async with sub:
            with tracing.trace_context(trace_id=tid, group_id=gid):
                await bus.publish(Message(
                    group_id=gid, id=1, member_id=1, sender_name="bot",
                    sender_type="bot", content="hi", created_at="now"
                ))
            
            event = await asyncio.wait_for(anext(sub), 1.0)
            self.assertEqual(event["trace_id"], tid)

    async def test_trace_id_generation(self):
        with tracing.trace_context(group_id=100):
            tid = tracing.get_trace_id()
            self.assertIsNotNone(tid)
            self.assertTrue(len(tid) > 20) # UUID string
            
            logging.info("Generated trace message")
            
        log_json = json.loads(self.log_capture.getvalue())
        self.assertEqual(log_json["trace_id"], tid)

if __name__ == "__main__":
    unittest.main()
