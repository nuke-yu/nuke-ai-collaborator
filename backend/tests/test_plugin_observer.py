"""tests/test_plugin_observer.py — Supervisor observer mechanism tests.

Verifies the plugin observer extension point:
  - register/unregister observers
  - observers receive broadcast events
  - slow/broken observers don't affect fanout or upstream processing
  - multiple observers work independently
"""
import pytest
from runtime.supervisor import Supervisor


@pytest.fixture
def sup():
    """Create a minimal Supervisor instance (no workers, no server)."""
    return Supervisor("dummy", num_workers=0)


class TestObserverRegistration:

    def test_register_observer(self, sup):
        """register_observer adds to _event_observers dict."""
        cb = lambda gid, payload: None
        sup.register_observer("test_plugin", cb)
        assert "test_plugin" in sup._event_observers
        assert sup._event_observers["test_plugin"] is cb

    def test_unregister_observer(self, sup):
        """unregister_observer removes from dict."""
        cb = lambda gid, payload: None
        sup.register_observer("test_plugin", cb)
        sup.unregister_observer("test_plugin")
        assert "test_plugin" not in sup._event_observers

    def test_unregister_nonexistent(self, sup):
        """unregister_observer is a no-op for unknown names."""
        sup.unregister_observer("nonexistent")  # should not raise

    def test_multiple_observers(self, sup):
        """Multiple observers can be registered independently."""
        cb1 = lambda gid, payload: None
        cb2 = lambda gid, payload: None
        sup.register_observer("plugin_a", cb1)
        sup.register_observer("plugin_b", cb2)
        assert len(sup._event_observers) == 2
        assert sup._event_observers["plugin_a"] is cb1
        assert sup._event_observers["plugin_b"] is cb2

    def test_replace_observer(self, sup):
        """Registering with the same name replaces the old callback."""
        cb1 = lambda gid, payload: "first"
        cb2 = lambda gid, payload: "second"
        sup.register_observer("test", cb1)
        sup.register_observer("test", cb2)
        assert sup._event_observers["test"] is cb2


class TestEmitToObservers:

    def test_observers_receive_events(self, sup):
        """_emit_to_observers calls all registered callbacks with correct args."""
        received = []
        sup.register_observer("test", lambda gid, p: received.append((gid, p)))
        sup._emit_to_observers(42, {"type": "tool_call", "tool_name": "read_file"})
        assert len(received) == 1
        assert received[0] == (42, {"type": "tool_call", "tool_name": "read_file"})

    def test_multiple_observers_all_called(self, sup):
        """All observers receive the same event."""
        results_a = []
        results_b = []
        sup.register_observer("a", lambda gid, p: results_a.append(gid))
        sup.register_observer("b", lambda gid, p: results_b.append(gid))
        sup._emit_to_observers(7, {"type": "stream_end"})
        assert results_a == [7]
        assert results_b == [7]

    def test_broken_observer_doesnt_affect_others(self, sup):
        """An observer that raises doesn't prevent other observers from receiving."""
        results = []

        def bad_observer(gid, payload):
            raise RuntimeError("observer crashed")

        def good_observer(gid, payload):
            results.append(gid)

        sup.register_observer("bad", bad_observer)
        sup.register_observer("good", good_observer)
        sup._emit_to_observers(1, {"type": "message"})
        # Good observer still received the event
        assert results == [1]

    def test_no_observers_no_error(self, sup):
        """_emit_to_observers is a no-op when no observers are registered."""
        sup._emit_to_observers(1, {"type": "tool_call"})  # should not raise

    def test_observer_receives_group_id_and_payload(self, sup):
        """Observer gets the exact group_id and payload dict."""
        captured = {}

        def capture(gid, payload):
            captured["gid"] = gid
            captured["payload"] = payload

        sup.register_observer("cap", capture)
        test_payload = {"type": "ai_thought_start", "iteration": 5}
        sup._emit_to_observers(99, test_payload)
        assert captured["gid"] == 99
        assert captured["payload"] is test_payload
