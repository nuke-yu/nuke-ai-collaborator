"""tests/test_coding_agent_orchestrator_v2.py — CodingAgentOrchestrator tests (core plugin).

Tests the orchestrator registered in core/orchestration/plugins/ so that
Worker processes discover it via registry.discover().
"""
import unittest
from core.orchestration.plugins.coding_agent import CodingAgentOrchestrator


class TestBegin(unittest.TestCase):

    def test_begin_returns_work_unit_immediately(self):
        """begin() must return WorkUnit so bot starts without separate trigger."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent", "executor_id": "tool_loop_v1"}
        step = orch.begin(1, {"bots": [bot], "requirements": "Build API", "test_command": "pytest"})

        self.assertTrue(step.broadcast_state)
        self.assertEqual(len(step.next_units), 1)
        unit = step.next_units[0]
        self.assertEqual(unit.bot["id"], 5)
        self.assertIn("Build API", unit.trigger_msg)
        self.assertIn("pytest", unit.trigger_msg)
        self.assertTrue(unit.is_workflow)

    def test_begin_empty_bots_marks_done(self):
        orch = CodingAgentOrchestrator()
        step = orch.begin(1, {"bots": []})
        self.assertTrue(step.done)

    def test_begin_with_test_command(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        step = orch.begin(1, {"bots": [bot], "requirements": "Fix bug", "test_command": "pytest -x"})
        unit = step.next_units[0]
        self.assertIn("pytest -x", unit.trigger_msg)


class TestObserve(unittest.TestCase):

    def test_observe_done_keyword(self):
        """observe() marks done only with explicit completion signal."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        step = orch.observe(1, 5, "All done! [[AGENT_DONE]]")
        self.assertTrue(step.done)

    def test_observe_no_signal_keeps_active(self):
        """Without completion signal, workflow stays active."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        step = orch.observe(1, 5, "I've written some code but haven't tested yet.")
        self.assertFalse(step.done)

    def test_observe_failure_signal_keeps_active(self):
        """Failure signals keep workflow active for retry."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        step = orch.observe(1, 5, "测试失败，无法修复这个 bug。[[AGENT_FAIL]]")
        self.assertFalse(step.done)

    def test_observe_signal_stage_done_tool(self):
        """signal_stage_done tool call marks done."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        step = orch.observe(1, 5, "Finished implementation.",
                            signals=[{"tool": "signal_stage_done", "reason": "done"}])
        self.assertTrue(step.done)

    def test_observe_signal_rework_keeps_active(self):
        """signal_rework tool call keeps workflow active."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        step = orch.observe(1, 5, "Need to rework.",
                            signals=[{"tool": "signal_rework", "reason": "tests failing"}])
        self.assertFalse(step.done)

    def test_observe_error_patterns(self):
        """Error patterns in response keep workflow active."""
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        step = orch.observe(1, 5, "PR 创建失败，请检查权限。")
        self.assertFalse(step.done)


class TestParseSpec(unittest.TestCase):

    def test_parse_spec_has_bots_key(self):
        """parse_spec must return 'bots' key to pass dispatch validation."""
        orch = CodingAgentOrchestrator()
        all_bots = {5: {"id": 5, "name": "Agent"}}
        spec = orch.parse_spec(
            {"bot_id": 5, "requirements": "Build API", "test_command": "pytest"},
            all_bots,
        )
        self.assertIn("bots", spec)
        self.assertEqual(len(spec["bots"]), 1)
        self.assertEqual(spec["bots"][0]["id"], 5)
        self.assertEqual(spec["requirements"], "Build API")

    def test_parse_spec_unknown_bot(self):
        orch = CodingAgentOrchestrator()
        spec = orch.parse_spec({"bot_id": 999, "requirements": "test", "test_command": ""}, {})
        self.assertEqual(spec["bots"], [])


class TestPersistence(unittest.TestCase):

    def test_serialize_restore(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent", "executor_id": "tool_loop_v1"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        state = orch.serialize(1)
        self.assertIsNotNone(state)

        orch2 = CodingAgentOrchestrator()
        orch2.restore(1, state)
        self.assertEqual(orch2.current_bot(1)["id"], 5)

    def test_resume_units_for_started_task(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent", "executor_id": "tool_loop_v1"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})

        units = orch.resume_units(1)
        self.assertEqual(len(units), 1)
        self.assertIn("Resume", units[0].trigger_msg)

    def test_resume_units_for_done_task(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bots": [bot], "requirements": "test", "test_command": ""})
        orch.observe(1, 5, "[[AGENT_DONE]]")

        units = orch.resume_units(1)
        self.assertEqual(len(units), 0)


class TestRegistryDiscovery(unittest.TestCase):

    def test_orchestrator_discoverable(self):
        """coding_agent_v1 must be discoverable by registry."""
        from core.orchestration import registry as orch_registry
        orch_registry.discover()
        orch = orch_registry.get("coding_agent_v1")
        self.assertEqual(orch.orchestrator_id, "coding_agent_v1")
        # Check class name instead of isinstance (dynamic import creates different class object)
        self.assertEqual(type(orch).__name__, "CodingAgentOrchestrator")
