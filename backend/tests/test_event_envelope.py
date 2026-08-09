import unittest

from runtime.event_envelope import make_event_envelope


class TestEventEnvelope(unittest.TestCase):
    def test_wraps_event_and_preserves_legacy_type(self):
        envelope = make_event_envelope({
            "type": "tool_result",
            "group_id": 7,
            "session_id": "session-1",
            "request_id": "request-1",
            "result": "ok",
        })
        self.assertEqual(envelope["protocol_version"], 1)
        self.assertEqual(envelope["event_type"], "tool_result")
        self.assertEqual(envelope["type"], "tool_result")
        self.assertEqual(envelope["payload"]["result"], "ok")
        self.assertEqual(envelope["session_id"], "session-1")

    def test_reuses_existing_observability_event_id(self):
        envelope = make_event_envelope({
            "type": "message",
            "_observability": {"event_id": "evt_existing"},
        }, group_id=3)
        self.assertEqual(envelope["event_id"], "evt_existing")
        self.assertEqual(envelope["group_id"], 3)


if __name__ == "__main__":
    unittest.main()
