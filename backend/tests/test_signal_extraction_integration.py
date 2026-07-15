"""tests/test_signal_extraction_integration.py — Integration test for signal extraction.

Verifies the full signal flow:
  1. cleanup_and_finalize() extracts signals from tool calls
  2. runner passes signals to orchestrator.observe()
  3. orchestrator makes correct completion decision
"""
import unittest
from core.orchestration.plugins.coding_agent import CodingAgentOrchestrator


class TestSignalExtractionIntegration(unittest.TestCase):
    """Integration test: signal extraction → orchestrator completion."""

    def test_signal_stage_done_from_tool_call(self):
        """Signal extracted from signal_stage_done tool call marks workflow done."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "Build API", "test_command": "pytest"})

        # Simulate signals extracted by cleanup_and_finalize from tool calls
        signals = [
            {
                "name": "signal_stage_done",
                "arguments": {"reason": "All tests pass, PR created at https://github.com/user/repo/pull/42"}
            }
        ]

        step = orch.observe(1, 5, "Implementation complete.", signals=signals)
        self.assertTrue(step.done)
        self.assertTrue(step.broadcast_state)

    def test_signal_rework_from_tool_call(self):
        """Signal extracted from signal_rework tool call keeps workflow active."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "Build API", "test_command": "pytest"})

        # Simulate signals extracted by cleanup_and_finalize from tool calls
        signals = [
            {
                "name": "signal_rework",
                "arguments": {"reason": "Tests failing: AssertionError in test_auth.py", "target_stage": "dev"}
            }
        ]

        step = orch.observe(1, 5, "Cannot fix the failing tests.", signals=signals)
        self.assertFalse(step.done)
        self.assertTrue(step.broadcast_state)
        self.assertEqual(step.workflow_paused.reason, "rework_requested")
        self.assertIn("AssertionError", step.workflow_paused.details)

    def test_no_signal_from_tool_call(self):
        """No completion signal extracted → WorkflowPaused."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "Build API", "test_command": "pytest"})

        # Simulate no completion signals (bot only called write_file, read_file, etc.)
        signals = []

        step = orch.observe(1, 5, "I've written some code.", signals=signals)
        self.assertFalse(step.done)
        self.assertIsNotNone(step.workflow_paused)
        self.assertEqual(step.workflow_paused.reason, "completion_signal_missing")

    def test_multiple_signals_first_wins(self):
        """If multiple signals present, first one determines outcome."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "Build API", "test_command": "pytest"})

        # Simulate both signals extracted (shouldn't happen, but test robustness)
        signals = [
            {"name": "signal_stage_done", "arguments": {"reason": "done"}},
            {"name": "signal_rework", "arguments": {"reason": "failed"}}
        ]

        step = orch.observe(1, 5, "Mixed signals.", signals=signals)
        # First signal (done) wins
        self.assertTrue(step.done)

    def test_signal_with_empty_arguments(self):
        """Signal with empty arguments still works."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "Build API", "test_command": "pytest"})

        signals = [
            {"name": "signal_stage_done", "arguments": {}}
        ]

        step = orch.observe(1, 5, "Done.", signals=signals)
        self.assertTrue(step.done)


class TestWorkflowSignalSchema(unittest.TestCase):
    """Verify WorkflowSignal schema is enforced."""

    def test_signal_must_have_name(self):
        """Signal without 'name' field is ignored."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        # Malformed signal (missing 'name')
        signals = [{"arguments": {"reason": "done"}}]

        step = orch.observe(1, 5, "Done.", signals=signals)
        # Signal ignored, workflow paused
        self.assertFalse(step.done)
        self.assertIsNotNone(step.workflow_paused)

    def test_signal_must_have_arguments(self):
        """Signal without 'arguments' field still works (arguments can be empty)."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        # Signal with no arguments field
        signals = [{"name": "signal_stage_done"}]

        step = orch.observe(1, 5, "Done.", signals=signals)
        # Should still work
        self.assertTrue(step.done)

    def test_unknown_signal_name_ignored(self):
        """Signal with unknown name is ignored."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        signals = [{"name": "unknown_signal", "arguments": {}}]

        step = orch.observe(1, 5, "Something.", signals=signals)
        self.assertFalse(step.done)
        self.assertIsNotNone(step.workflow_paused)
