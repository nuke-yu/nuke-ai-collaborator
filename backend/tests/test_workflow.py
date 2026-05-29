import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database

# Use a test database to isolate tests
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_chat.db")
database.DB_PATH = TEST_DB_PATH


def _bot_entry(bot_id=2, name="WorkflowBot"):
    return {
        "id": bot_id, "name": name, "role": "Developer",
        "avatar_color": "#123456", "system_prompt": "You are workflow bot",
        "personality_prompt": "",
    }


class TestOrchestratorFlow(unittest.TestCase):
    """编排层是纯决策：给定状态 + 产出，返回 OrchestratorStep，不发事件、不调 AI。"""

    def setUp(self):
        from core.orchestration.declarative import DeclarativeOrchestrator
        self.orch = DeclarativeOrchestrator()

    def _single(self, bid, name, keyword="完毕", executor_id=None):
        s = {"id": bid, "name": name, "avatar_color": "#111",
             "stage_type": "single", "done_keyword": keyword, "role": "Dev"}
        if executor_id:
            s["executor_id"] = executor_id
        return s

    def test_single_advances_on_keyword(self):
        self.orch.begin(1, [self._single(1, "A"), self._single(2, "B")])
        step = self.orch.observe(1, 1, "做完了 完毕")
        self.assertTrue(step.broadcast_state)
        self.assertEqual(len(step.next_units), 1)
        self.assertEqual(step.next_units[0].bot["id"], 2)
        self.assertFalse(step.done)

    def test_single_no_keyword_no_advance(self):
        self.orch.begin(1, [self._single(1, "A"), self._single(2, "B")])
        step = self.orch.observe(1, 1, "还在沟通中")
        self.assertEqual(step.next_units, [])
        self.assertFalse(step.done)

    def test_final_stage_marks_done_and_clears(self):
        self.orch.begin(1, [self._single(1, "A")])
        step = self.orch.observe(1, 1, "总结完毕")
        self.assertTrue(step.done)
        self.assertIsNone(self.orch.get(1))

    def test_workunit_carries_executor_id(self):
        self.orch.begin(1, [self._single(1, "A"), self._single(2, "B", executor_id="tool_loop_v1")])
        step = self.orch.observe(1, 1, "完毕")
        self.assertEqual(step.next_units[0].executor_id, "tool_loop_v1")

    def test_pool_entry_assigns_tickets(self):
        pool = {"stage_type": "pool", "done_keyword": "完毕",
                "bots": [_bot_entry(10, "D1"), _bot_entry(11, "D2")]}
        self.orch.begin(1, [self._single(1, "A"), pool])
        # single A finishes and emits a TICKETS list → entering the pool
        resp = "方案如下\nTICKETS:\n1. 任务甲\n2. 任务乙\n3. 任务丙\n完毕"
        step = self.orch.observe(1, 1, resp)
        # 2 bots each pick up one ticket initially, 1 queued
        self.assertEqual(len(step.next_units), 2)
        self.assertEqual(len(pool["in_progress"]), 2)
        self.assertEqual(pool["ticket_queue"], ["任务丙"])
        self.assertEqual(len(step.announcements), 1)


class TestRunnerBroadcast(unittest.IsolatedAsyncioTestCase):
    """runner.run_unit 把 broadcaster=bus 接给 executor，stream 事件经总线带 'delta'。"""

    async def test_run_unit_streams_delta_via_executor(self):
        from core import runner
        from executors.base import ExecutionResult
        from core.orchestration.base import OrchestratorStep
        from core.orchestration.base import WorkUnit

        class FakeExec:
            executor_id = "fake"

            async def run(self, ctx):
                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "stream_chunk", "temp_id": "x", "delta": "Part 1",
                })
                return ExecutionResult(full_text="Part 1", msg_id=99)

        class StubOrch:
            def observe(self, gid, bid, resp):
                return OrchestratorStep()

            def snapshot(self, gid):
                return {"active": False}

        captured = []

        async def cap(group_id, payload):
            captured.append(payload)

        bot = _bot_entry()
        unit = WorkUnit(bot=bot, executor_id="fake", trigger_msg="go", prompt_suffix="[stage]")

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(runner, "get_members", new=AsyncMock(return_value=[bot])), \
             patch.object(runner, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(runner, "get_db", return_value=fake_db_cm), \
             patch.object(runner.exec_registry, "get", return_value=FakeExec()), \
             patch.object(runner.bus, "broadcast", new=cap):
            await runner.run_unit(1, unit, StubOrch())

        chunks = [e for e in captured if e.get("type") == "stream_chunk"]
        self.assertTrue(len(chunks) > 0)
        for e in chunks:
            self.assertIn("delta", e)
            self.assertNotIn("chunk", e)
            self.assertEqual(e["delta"], "Part 1")

    async def test_run_unit_maps_workunit_to_context(self):
        """契约核心：WorkUnit 的 trigger_msg/prompt_suffix 映射到 ExecutionContext，broadcaster=bus。"""
        from core import runner
        from executors.base import ExecutionResult
        from core.orchestration.base import OrchestratorStep, WorkUnit

        captured = {}

        class FakeExec:
            executor_id = "fake"

            async def run(self, ctx):
                captured["ctx"] = ctx
                return ExecutionResult(full_text="done", msg_id=1)

        class StubOrch:
            def observe(self, gid, bid, resp):
                return OrchestratorStep()

            def snapshot(self, gid):
                return {"active": False}

        bot = _bot_entry()
        unit = WorkUnit(bot=bot, executor_id="fake",
                        trigger_msg="请开始你的工作。", prompt_suffix="[阶段指令]")

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(runner, "get_members", new=AsyncMock(return_value=[bot])), \
             patch.object(runner, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(runner, "get_db", return_value=fake_db_cm), \
             patch.object(runner.exec_registry, "get", return_value=FakeExec()):
            await runner.run_unit(1, unit, StubOrch())

        ctx = captured["ctx"]
        self.assertEqual(ctx.user_message, "请开始你的工作。")
        self.assertEqual(ctx.workflow_suffix, "[阶段指令]")
        self.assertIs(ctx.broadcaster, runner.bus)


class TestWorkflowParseTickets(unittest.TestCase):
    def test_parse_tickets_numbered_header(self):
        from core.workflow import _parse_tickets
        msg = "Here are the tickets:\nTICKETS:\n1. Fix login bug\n2. Add settings page\nSome other text"
        self.assertEqual(_parse_tickets(msg), ["Fix login bug", "Add settings page"])

    def test_parse_tickets_bullet_header_hyphen(self):
        from core.workflow import _parse_tickets
        msg = "TICKETS:\n- Implement OAuth\n- Style layout"
        self.assertEqual(_parse_tickets(msg), ["Implement OAuth", "Style layout"])

    def test_parse_tickets_bullet_header_asterisk(self):
        from core.workflow import _parse_tickets
        msg = "TICKETS:\n* Setup CI/CD\n* Add unit tests"
        self.assertEqual(_parse_tickets(msg), ["Setup CI/CD", "Add unit tests"])

    def test_parse_tickets_no_header_numbered(self):
        from core.workflow import _parse_tickets
        msg = "We need to do:\n1. Write README\n2. Push to main"
        self.assertEqual(_parse_tickets(msg), ["Write README", "Push to main"])

    def test_parse_tickets_no_header_bullets(self):
        from core.workflow import _parse_tickets
        msg = "To-do list:\n- Create logo\n- Write backend code"
        self.assertEqual(_parse_tickets(msg), ["Create logo", "Write backend code"])

    def test_parse_tickets_fallback(self):
        from core.workflow import _parse_tickets
        msg = "No tickets list found here"
        self.assertEqual(_parse_tickets(msg), ["本次迭代任务"])


if __name__ == "__main__":
    unittest.main()
