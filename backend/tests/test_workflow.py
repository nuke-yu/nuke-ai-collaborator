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

    def test_pool_expertise_priority_claim(self):
        """擅长领域的 bot 高优先级认领对应任务；无人擅长的任务回到默认认领。"""
        fe = {**_bot_entry(10, "FE"), "expertise": ["前端", "UI"]}
        be = {**_bot_entry(11, "BE"), "expertise": ["后端", "API"]}
        pool = {"stage_type": "pool", "done_keyword": "完毕", "bots": [fe, be]}
        self.orch.begin(1, [self._single(1, "A"), pool])

        resp = "TICKETS:\n1. 实现登录 API 鉴权\n2. 搭建首页 UI\n完毕"
        self.orch.observe(1, 1, resp)
        # 命中领域优先：后端领 API 任务，前端领 UI 任务（与任务列表顺序无关）
        self.assertEqual(pool["in_progress"][11], "实现登录 API 鉴权")
        self.assertEqual(pool["in_progress"][10], "搭建首页 UI")

    def test_pool_no_expertise_falls_back_to_default(self):
        """都不擅长时回到默认：每人仍各领一个，不报错。"""
        pool = {"stage_type": "pool", "done_keyword": "完毕",
                "bots": [_bot_entry(10, "D1"), _bot_entry(11, "D2")]}
        self.orch.begin(1, [self._single(1, "A"), pool])
        self.orch.observe(1, 1, "TICKETS:\n1. 任务甲\n2. 任务乙\n完毕")
        self.assertEqual(len(pool["in_progress"]), 2)
        self.assertEqual(set(pool["in_progress"].values()), {"任务甲", "任务乙"})

    def test_pluggable_discussion_stage(self):
        """插拔验证：discussion 是注册进来的新 stage_type，编排器核心未改动也能驱动它。

        讨论 = 串行轮流发言（round-robin）+ 轮次终止，与 single/pool 都不同。
        """
        from core.orchestration import all_stage_types
        self.assertIn("discussion", all_stage_types())

        bots = [_bot_entry(30, "X"), _bot_entry(31, "Y"), _bot_entry(32, "Z")]
        disc = {"stage_type": "discussion", "rounds": 5, "bots": bots}
        self.orch.begin(1, [self._single(1, "A"), disc])

        # single A 完成 → 进入讨论：只派 1 个 WorkUnit（第 1 位发言者）
        step = self.orch.observe(1, 1, "完毕")
        self.assertEqual(len(step.next_units), 1)
        self.assertEqual(step.next_units[0].bot["id"], 30)
        self.assertEqual(disc["round"], 1)
        # 路由：当前只有发言者一人 active
        self.assertEqual(self.orch.current_pool_bots(1), [30])
        self.assertIsNone(self.orch.current_bot(1))

        # round-robin：X→Y→Z→X→Y，每次只下一位
        order = [30, 31, 32, 30]
        for i, speaker in enumerate(order):
            s = self.orch.observe(1, speaker, "我说完了")
            self.assertFalse(s.done)
            self.assertEqual(len(s.next_units), 1)
            self.assertEqual(s.next_units[0].bot["id"], order[i + 1] if i + 1 < len(order) else 31)
        self.assertEqual(disc["round"], 5)

        # 非当前发言者的消息被忽略（不推进）
        ignored = self.orch.observe(1, 32, "插嘴")
        self.assertEqual(ignored.next_units, [])

        # 第 5 轮（最后一轮）发言者 Y 完成 → 这是最后阶段 → done
        final = self.orch.observe(1, 31, "最终结论")
        self.assertTrue(final.done)
        self.assertIsNone(self.orch.get(1))

    def test_pluggable_verification_stage(self):
        """插拔验证：verification 两阶段（出方案→投票）的新 stage_type，编排器核心未改动。"""
        from core.orchestration import all_stage_types
        self.assertIn("verification", all_stage_types())

        bots = [_bot_entry(40, "Alpha"), _bot_entry(41, "Beta"), _bot_entry(42, "Gamma")]
        ver = {"stage_type": "verification", "bots": bots}
        self.orch.begin(1, [self._single(1, "A"), ver])

        # A 完成 → propose 阶段：3 个方案 unit 并行
        step = self.orch.observe(1, 1, "完毕")
        self.assertEqual(ver["phase"], "propose")
        self.assertEqual(len(step.next_units), 3)
        self.assertEqual(set(self.orch.current_pool_bots(1)), {40, 41, 42})

        # 三人各出方案，最后一人触发进入投票
        self.orch.observe(1, 40, "方案甲")
        self.orch.observe(1, 41, "方案乙")
        s = self.orch.observe(1, 42, "方案丙")
        self.assertEqual(ver["phase"], "vote")
        self.assertEqual(len(s.next_units), 3)
        self.assertEqual(ver["proposals"][41], "方案乙")

        # 投票：Beta 得 2 票胜出
        self.orch.observe(1, 40, "我选 投票：Beta")
        self.orch.observe(1, 41, "投票：Beta")
        final = self.orch.observe(1, 42, "投票：Alpha")

        # 最后阶段 → done；winner=41(Beta)
        self.assertTrue(final.done)
        self.assertEqual(ver["winner"], 41)
        self.assertIsNone(self.orch.get(1))


class TestWorkflowPersistence(unittest.TestCase):
    """崩溃恢复：serialize → JSON round-trip → restore 必须保住状态，
    并能 resume_units 重新派发在飞工作单元。重点测 int bot_id key 经 JSON 后的修复。"""

    def setUp(self):
        from core.orchestration.declarative import DeclarativeOrchestrator
        self.orch = DeclarativeOrchestrator()

    def _roundtrip(self, group_id):
        """模拟落盘 + 重启：serialize → JSON dumps/loads → 新编排器 restore。"""
        import json
        from core.orchestration.declarative import DeclarativeOrchestrator
        blob = json.loads(json.dumps(self.orch.serialize(group_id), ensure_ascii=False))
        fresh = DeclarativeOrchestrator()
        fresh.restore(group_id, blob)
        return fresh

    def _single(self, bid, name, keyword="完毕"):
        return {"id": bid, "name": name, "avatar_color": "#111",
                "stage_type": "single", "done_keyword": keyword, "role": "Dev"}

    def test_single_serialize_restore_keeps_position(self):
        self.orch.begin(1, [self._single(1, "A"), self._single(2, "B")])
        self.orch.observe(1, 1, "完毕")  # 推进到第二阶段
        fresh = self._roundtrip(1)
        self.assertEqual(fresh.get(1)["current"], 1)
        self.assertEqual(fresh.current_bot(1)["id"], 2)
        # single 由用户对话驱动，恢复后不重派单元
        self.assertEqual(fresh.resume_units(1), [])

    def test_pool_rehydrate_int_keys_and_resume(self):
        """pool 的 in_progress 用 int bot_id 做 key，JSON 后变 str —— restore 必须还原。"""
        pool = {"stage_type": "pool", "done_keyword": "完毕",
                "bots": [_bot_entry(10, "D1"), _bot_entry(11, "D2")]}
        self.orch.begin(1, [self._single(1, "A"), pool])
        self.orch.observe(1, 1, "TICKETS:\n1. 任务甲\n2. 任务乙\n完毕")

        fresh = self._roundtrip(1)
        ip = fresh.get(1)["stages"][1]["in_progress"]
        self.assertEqual(set(ip.keys()), {10, 11})  # int, 不是 "10"/"11"
        # resume 重新派发两个在飞 ticket 的 unit
        units = fresh.resume_units(1)
        self.assertEqual(len(units), 2)
        self.assertEqual({u.bot["id"] for u in units}, {10, 11})
        # 恢复后 observe 仍能按 int key 命中并推进
        step = fresh.observe(1, 10, "完毕")
        self.assertNotIn(10, fresh.get(1)["stages"][1]["in_progress"])
        self.assertTrue(step.broadcast_state)

    def test_discussion_resume_redispatches_current_speaker(self):
        bots = [_bot_entry(30, "X"), _bot_entry(31, "Y")]
        disc = {"stage_type": "discussion", "rounds": 4, "bots": bots}
        self.orch.begin(1, [self._single(1, "A"), disc])
        self.orch.observe(1, 1, "完毕")        # speaker=30, round=1
        self.orch.observe(1, 30, "我说完了")   # speaker=31, round=2

        fresh = self._roundtrip(1)
        units = fresh.resume_units(1)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].bot["id"], 31)
        self.assertEqual(fresh.get(1)["stages"][1]["round"], 2)

    def test_verification_rehydrate_and_resume(self):
        bots = [_bot_entry(40, "Alpha"), _bot_entry(41, "Beta"), _bot_entry(42, "Gamma")]
        ver = {"stage_type": "verification", "bots": bots}
        self.orch.begin(1, [self._single(1, "A"), ver])
        self.orch.observe(1, 1, "完毕")        # propose, pending=[40,41,42]
        self.orch.observe(1, 40, "方案甲")     # pending=[41,42], proposals{40:...}

        fresh = self._roundtrip(1)
        stage = fresh.get(1)["stages"][1]
        self.assertEqual(set(stage["proposals"].keys()), {40})  # int key 还原
        self.assertEqual(stage["phase"], "propose")
        # resume 重派两个未出方案的人
        units = fresh.resume_units(1)
        self.assertEqual({u.bot["id"] for u in units}, {41, 42})
        # 恢复后继续：剩两人出完方案 → 进入投票
        fresh.observe(1, 41, "方案乙")
        s = fresh.observe(1, 42, "方案丙")
        self.assertEqual(fresh.get(1)["stages"][1]["phase"], "vote")
        self.assertEqual(len(s.next_units), 3)

    def test_serialize_none_after_end(self):
        self.orch.begin(1, [self._single(1, "A")])
        self.orch.observe(1, 1, "完毕")  # 最后阶段 → done → end → state 清空
        self.assertIsNone(self.orch.serialize(1))


class TestWorkflowStoreDB(unittest.IsolatedAsyncioTestCase):
    """workflow_store 的落盘/读取/清除（真实 SQLite，验证 ON CONFLICT 覆写与 active 过滤）。"""

    async def asyncSetUp(self):
        await database.init_db()
        from core import workflow_store
        self.store = workflow_store
        # FK(groups) 已开启 —— 先确保 group 行存在
        async with database.connect() as conn:
            await conn.execute("INSERT OR IGNORE INTO groups (id, name) VALUES (?, ?)", (901, "wf-test"))
            await conn.execute("DELETE FROM workflow_state WHERE group_id = ?", (901,))
            await conn.commit()

    async def asyncTearDown(self):
        async with database.connect() as conn:
            await conn.execute("DELETE FROM workflow_state WHERE group_id = ?", (901,))
            await conn.commit()

    async def test_save_load_clear_roundtrip(self):
        state = {"stages": [{"stage_type": "single"}], "current": 0}
        await self.store.save_state(901, "workflow_v1", state)
        rows = await self.store.load_all_active()
        mine = [r for r in rows if r["group_id"] == 901]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["orchestrator_id"], "workflow_v1")
        self.assertEqual(mine[0]["state"], state)

        # ON CONFLICT 覆写：同 group 再存一次只更新，不新增行
        await self.store.save_state(901, "workflow_v1", {"stages": [], "current": 3})
        rows = await self.store.load_all_active()
        mine = [r for r in rows if r["group_id"] == 901]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["state"]["current"], 3)

        await self.store.clear_state(901)
        rows = await self.store.load_all_active()
        self.assertEqual([r for r in rows if r["group_id"] == 901], [])


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
