"""tests/test_coding_agent_orchestrator.py — CodingAgentOrchestrator unit tests.

Tests the single-bot autonomous orchestrator:
  - begin() / dispatch() / observe() lifecycle
  - serialize() / restore() for crash recovery
  - resume_units() for in-flight task recovery
  - parse_spec() for workflow start body parsing
"""
import unittest
from plugins.agent_dashboard.coding_agent_orchestrator import CodingAgentOrchestrator


class TestLifecycle(unittest.TestCase):

    def test_begin_stores_state(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Coding Agent", "executor_id": "tool_loop_v1"}
        step = orch.begin(1, {"bot": bot, "requirements": "Build API", "test_command": "pytest"})

        self.assertTrue(step.broadcast_state)
        state = orch._state[1]
        self.assertEqual(state["bot"]["id"], 5)
        self.assertEqual(state["requirements"], "Build API")
        self.assertFalse(state["started"])
        self.assertFalse(state["done"])

    def test_begin_empty_bot_marks_done(self):
        orch = CodingAgentOrchestrator()
        step = orch.begin(1, {"bot": {}})
        self.assertTrue(step.done)

class TestLifecycleAsync(unittest.IsolatedAsyncioTestCase):

    async def test_dispatch_creates_work_unit(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Coding Agent", "executor_id": "tool_loop_v1"}
        orch.begin(1, {"bot": bot, "requirements": "Build REST API", "test_command": "pytest -x"})

        step = await orch.dispatch(1, {"content": "start"}, [], [])
        self.assertTrue(step.broadcast_state)
        self.assertEqual(len(step.next_units), 1)

        unit = step.next_units[0]
        self.assertEqual(unit.bot["id"], 5)
        self.assertIn("Build REST API", unit.trigger_msg)
        self.assertIn("pytest -x", unit.trigger_msg)
        self.assertTrue(unit.is_workflow)

    async def test_dispatch_only_runs_once(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bot": bot, "requirements": "test", "test_command": ""})

        await orch.dispatch(1, {}, [], [])
        step2 = await orch.dispatch(1, {}, [], [])
        self.assertEqual(len(step2.next_units), 0)  # second dispatch is no-op

    async def test_observe_marks_done(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bot": bot, "requirements": "test", "test_command": ""})
        await orch.dispatch(1, {}, [], [])

        step = orch.observe(1, 5, "Done! PR created.")
        self.assertTrue(step.done)
        self.assertTrue(step.broadcast_state)
        self.assertNotIn(1, orch._state)  # state cleaned up after end()


class TestSnapshot(unittest.TestCase):

    def test_snapshot_active(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bot": bot, "requirements": "test", "test_command": ""})

        snap = orch.snapshot(1)
        self.assertTrue(snap["active"])
        self.assertEqual(snap["type"], "coding_agent")
        self.assertEqual(snap["bot"]["id"], 5)

    def test_snapshot_inactive(self):
        orch = CodingAgentOrchestrator()
        snap = orch.snapshot(999)
        self.assertFalse(snap["active"])


class TestPersistence(unittest.TestCase):

    def test_serialize_returns_state(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bot": bot, "requirements": "test", "test_command": ""})

        state = orch.serialize(1)
        self.assertIsNotNone(state)
        self.assertEqual(state["bot"]["id"], 5)
        self.assertEqual(state["requirements"], "test")

    def test_serialize_nonexistent_returns_none(self):
        orch = CodingAgentOrchestrator()
        self.assertIsNone(orch.serialize(999))

    def test_restore_rebuilds_state(self):
        orch = CodingAgentOrchestrator()
        state = {
            "bot": {"id": 5, "name": "Agent"},
            "started": True,
            "done": False,
            "start_time": "2026-01-01 00:00:00",
            "requirements": "Build API",
            "test_command": "pytest",
        }
        orch.restore(1, state)
        self.assertEqual(orch._state[1]["bot"]["id"], 5)
        self.assertTrue(orch._state[1]["started"])

    def test_resume_units_for_started_task(self):
        orch = CodingAgentOrchestrator()
        state = {
            "bot": {"id": 5, "name": "Agent", "executor_id": "tool_loop_v1"},
            "started": True,
            "done": False,
            "start_time": "2026-01-01 00:00:00",
            "requirements": "Build API",
            "test_command": "",
        }
        orch.restore(1, state)
        units = orch.resume_units(1)
        self.assertEqual(len(units), 1)
        self.assertIn("Resume", units[0].trigger_msg)
        self.assertTrue(units[0].is_workflow)

    def test_resume_units_for_done_task(self):
        orch = CodingAgentOrchestrator()
        state = {
            "bot": {"id": 5, "name": "Agent"},
            "started": True,
            "done": True,
            "start_time": "2026-01-01 00:00:00",
            "requirements": "Build API",
            "test_command": "",
        }
        orch.restore(1, state)
        units = orch.resume_units(1)
        self.assertEqual(len(units), 0)  # done tasks don't resume

    def test_resume_units_for_not_started(self):
        orch = CodingAgentOrchestrator()
        state = {
            "bot": {"id": 5, "name": "Agent"},
            "started": False,
            "done": False,
            "start_time": "2026-01-01 00:00:00",
            "requirements": "Build API",
            "test_command": "",
        }
        orch.restore(1, state)
        units = orch.resume_units(1)
        self.assertEqual(len(units), 0)  # not-started tasks wait for dispatch


class TestParseSpec(unittest.TestCase):

    def test_parse_spec_with_bot(self):
        orch = CodingAgentOrchestrator()
        all_bots = {5: {"id": 5, "name": "Agent"}}
        body = {"bot_id": 5, "requirements": "Build API", "test_command": "pytest"}

        spec = orch.parse_spec(body, all_bots)
        self.assertEqual(spec["bot"]["id"], 5)
        self.assertEqual(spec["requirements"], "Build API")
        self.assertEqual(spec["test_command"], "pytest")

    def test_parse_spec_unknown_bot(self):
        orch = CodingAgentOrchestrator()
        body = {"bot_id": 999, "requirements": "test", "test_command": ""}
        spec = orch.parse_spec(body, {})
        self.assertEqual(spec["bot"], {})  # empty bot for unknown ID


class TestQueryMethods(unittest.TestCase):

    def test_current_bot(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bot": bot, "requirements": "test", "test_command": ""})
        self.assertEqual(orch.current_bot(1)["id"], 5)

    def test_current_bot_nonexistent(self):
        orch = CodingAgentOrchestrator()
        self.assertIsNone(orch.current_bot(999))

    def test_start_time(self):
        orch = CodingAgentOrchestrator()
        bot = {"id": 5, "name": "Agent"}
        orch.begin(1, {"bot": bot, "requirements": "test", "test_command": ""})
        self.assertIsNotNone(orch.start_time(1))

    def test_system_suffix_empty(self):
        orch = CodingAgentOrchestrator()
        self.assertEqual(orch.system_suffix(1), "")
