import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _writer_mod

# Use a test database to isolate tests
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_chat.db")
database.DB_PATH = TEST_DB_PATH
# workflow_store writes through the serialized writer (db.write_connect), which
# resolves its default from db.writer.DB_PATH — a separate module global. Patch it too.
_writer_mod.DB_PATH = TEST_DB_PATH


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

    # ── 人确认门（gate）─────────────────────────────────────────────────────────
    def _gated(self, bid, name, keyword="完毕"):
        s = self._single(bid, name, keyword=keyword)
        s["gate"] = True
        s["gate_label"] = f"确认{name}"
        return s

    def test_gated_stage_raises_gate_instead_of_advancing(self):
        self.orch.begin(1, [self._gated(1, "BA"), self._single(2, "Dev")])
        step = self.orch.observe(1, 1, "需求整理好了 完毕")
        # 挂起：发卡片、不推进、不派发下一个 bot
        self.assertIsNotNone(step.confirm_gate)
        self.assertEqual(step.confirm_gate["gate_id"], "1-0")
        self.assertEqual(step.confirm_gate["bot_id"], 1)
        self.assertEqual(step.confirm_gate["label"], "确认BA")
        self.assertEqual(step.next_units, [])
        self.assertFalse(step.done)
        # 仍停在第 0 阶段，快照标记挂起
        self.assertEqual(self.orch.get(1)["current"], 0)
        self.assertEqual(self.orch.snapshot(1)["awaiting_confirm"], "1-0")

    def test_confirm_advances_past_gate(self):
        self.orch.begin(1, [self._gated(1, "BA"), self._single(2, "Dev")])
        self.orch.observe(1, 1, "完毕")
        step = self.orch.confirm(1)
        self.assertEqual(len(step.next_units), 1)
        self.assertEqual(step.next_units[0].bot["id"], 2)   # 交棒给 Dev
        self.assertEqual(self.orch.get(1)["current"], 1)
        self.assertIsNone(self.orch.snapshot(1)["awaiting_confirm"])

    def test_confirm_rejects_stale_gate_id(self):
        self.orch.begin(1, [self._gated(1, "BA"), self._single(2, "Dev")])
        self.orch.observe(1, 1, "完毕")
        stale = self.orch.confirm(1, gate_id="1-999")
        self.assertEqual(stale.next_units, [])
        self.assertEqual(self.orch.get(1)["current"], 0)            # 没被推进
        ok = self.orch.confirm(1, gate_id="1-0")                    # 对的 gate_id 才生效
        self.assertEqual(ok.next_units[0].bot["id"], 2)

    def test_confirm_without_pending_gate_is_noop(self):
        self.orch.begin(1, [self._gated(1, "BA"), self._single(2, "Dev")])
        step = self.orch.confirm(1)   # 还没挂门
        self.assertEqual(step.next_units, [])
        self.assertEqual(self.orch.get(1)["current"], 0)

    def test_gated_final_stage_done_only_after_confirm(self):
        self.orch.begin(1, [self._gated(1, "QA")])
        step = self.orch.observe(1, 1, "测试通过 完毕")
        self.assertFalse(step.done)                 # 挂门，先不结束
        self.assertIsNotNone(step.confirm_gate)
        done_step = self.orch.confirm(1)
        self.assertTrue(done_step.done)             # 确认后才结束
        self.assertIsNone(self.orch.get(1))

    def test_gated_snapshot_is_workflow_update_compatible(self):
        # 回归：snapshot 多出的 awaiting_confirm 必须能塞进 WorkflowUpdate 事件
        # （runner/workflow.apply 做的就是 WorkflowUpdate(group_id, **snapshot)）。
        from bus.events import WorkflowUpdate
        self.orch.begin(1, [self._gated(1, "BA")])
        self.orch.observe(1, 1, "完毕")            # 挂门 → snapshot 带 awaiting_confirm
        snap = self.orch.snapshot(1)
        ev = WorkflowUpdate(group_id=1, **snap)    # 不应抛 TypeError
        self.assertEqual(ev.awaiting_confirm, "1-0")

    def test_ungated_stage_still_auto_advances(self):
        # 回归：没有 gate 标志的阶段保持原行为（命中关键词即推进）
        self.orch.begin(1, [self._single(1, "A"), self._single(2, "B")])
        step = self.orch.observe(1, 1, "完毕")
        self.assertIsNone(step.confirm_gate)
        self.assertEqual(step.next_units[0].bot["id"], 2)

    # ── RD 流水线：3 道门 + 交棒（BA→Dev→QA）──────────────────────────────────────
    # 澄清 / 确认 / 建 Jira 工单都是 BA 一个人的活，合并成单一 BA 阶段（不再拆两段）。
    def test_rd_pipeline_three_gates_with_handoff(self):
        from core.orchestration.pipeline import build_rd_pipeline
        ba = {"id": 1, "name": "BA", "avatar_color": "#1", "role": "BA"}
        dev = {"id": 2, "name": "Dev", "avatar_color": "#2", "role": "Dev"}
        qa = {"id": 3, "name": "QA", "avatar_color": "#3", "role": "QA"}
        stages = build_rd_pipeline(ba, dev, qa)
        self.assertEqual(len(stages), 3)                        # BA 只出现一次
        self.assertEqual([st["id"] for st in stages], [1, 2, 3])
        self.orch.begin(1, stages)

        # 首阶段(BA)由用户驱动、begin 不派发 enter，建 Jira 工单的指令必须随
        # system_suffix 一起带给 BA（否则 BA 不知道要建工单）；推进信号是哨兵标记。
        suffix = self.orch.system_suffix(1)
        self.assertIn("Jira", suffix)
        self.assertIn("[[BA_DONE]]", suffix)

        # 阶段0 BA（澄清+建工单一气呵成）：吐出哨兵标记 → 门1（不推进）
        s = self.orch.observe(1, 1, "需求总结 + 工单清单…… 是否让开发开始？\n[[BA_DONE]]")
        self.assertEqual(s.confirm_gate["gate_id"], "1-0")
        self.assertEqual(self.orch.get(1)["current"], 0)

        # 确认门1 → 阶段1 Dev：交棒给 Dev，trigger 带「开发」指令
        s = self.orch.confirm(1)
        self.assertEqual(s.next_units[0].bot["id"], 2)          # 交棒给 Dev
        self.assertIn("开发", s.next_units[0].trigger_msg)
        self.assertEqual(self.orch.get(1)["current"], 1)

        # 阶段1 → 门2 → 阶段2 QA
        s = self.orch.observe(1, 2, "实现方案…… [[DEV_DONE]]")
        self.assertEqual(s.confirm_gate["gate_id"], "1-1")
        s = self.orch.confirm(1)
        self.assertEqual(s.next_units[0].bot["id"], 3)          # 交棒给 QA

        # 阶段2 QA（末棒）→ 门3：确认前不结束
        s = self.orch.observe(1, 3, "逐条AC…… [[QA_DONE]]")
        self.assertIsNotNone(s.confirm_gate)
        self.assertFalse(s.done)
        # 确认门3 → 整条流水线结束、状态清空
        s = self.orch.confirm(1)
        self.assertTrue(s.done)
        self.assertIsNone(self.orch.get(1))

    def test_sentinel_match_is_tolerant(self):
        """哨兵标记容错：大小写/空格/全角括号变体仍能命中；普通中文关键词不误伤。"""
        from core.orchestration.pipeline import build_rd_pipeline
        ba = {"id": 1, "name": "BA", "avatar_color": "#1", "role": "BA"}
        dev = {"id": 2, "name": "Dev", "avatar_color": "#2", "role": "Dev"}
        qa = {"id": 3, "name": "QA", "avatar_color": "#3", "role": "QA"}
        self.orch.begin(1, build_rd_pipeline(ba, dev, qa))
        # 模型把 [[BA_DONE]] 写成带空格小写的 [[ ba_done ]] —— 仍应挂门
        s = self.orch.observe(1, 1, "都就绪了，是否开始开发？\n[[ ba_done ]]")
        self.assertIsNotNone(s.confirm_gate)
        self.assertEqual(s.confirm_gate["gate_id"], "1-0")

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


class TestRecoveryWorkflowCoordination(unittest.IsolatedAsyncioTestCase):
    """崩溃恢复与工作流编排的协同：recover_all 走 _dispatch_recovery 绕过了
    run_unit/check_and_advance，恢复完成后必须把"在岗参与者"的产出 observe 回编排器，
    否则工作流卡死在当前阶段。非参与者 / 子代理（parent_id）不应推进。"""

    def _single(self, bid, name, keyword="完毕"):
        return {"id": bid, "name": name, "avatar_color": "#111",
                "stage_type": "single", "done_keyword": keyword, "role": "Dev"}

    def test_is_workflow_participant(self):
        import core.workflow as wf
        gid = 7001
        wf._orch.end(gid)
        wf.start(gid, [self._single(1, "A"), self._single(2, "B")])
        try:
            self.assertTrue(wf.is_workflow_participant(gid, 1))   # 当前单阶段 bot
            self.assertFalse(wf.is_workflow_participant(gid, 2))  # 下一阶段，未在岗
            self.assertFalse(wf.is_workflow_participant(999, 1))  # 无活跃工作流
        finally:
            wf._orch.end(gid)

    async def _run_recovery(self, gid, bot_id, parent_id, full_text):
        """跑一次 _dispatch_recovery，patch 掉 DB/执行器/ws，并把 check_and_advance
        换成 AsyncMock 以隔离"是否推进"这一决策（编排器内部流转已在别处测过）。"""
        import db as dbmod
        import core.workflow as wf
        from sessions import recovery
        from executors import registry as exec_reg
        from executors.base import ExecutionResult
        from ws_manager import manager as ws_manager

        class FakeExec:
            async def run(self, ctx):
                return ExecutionResult(full_text=full_text, msg_id=5)

        bot = {"id": bot_id, "name": "A", "type": "bot", "avatar_color": "#111", "role": "Dev"}
        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        payload = {
            "session_id": "s1", "bot_id": bot_id, "group_id": gid,
            "config": {}, "user_message": "", "messages": [],
            "parent_id": parent_id, "executor_id": "tool_loop_v1",
        }
        advance = AsyncMock()
        with patch.object(dbmod, "get_member", new=AsyncMock(return_value=bot)), \
             patch.object(dbmod, "get_members", new=AsyncMock(return_value=[bot])), \
             patch.object(dbmod, "get_messages", new=AsyncMock(return_value=[])), \
             patch.object(dbmod, "get_db", return_value=fake_db_cm), \
             patch.object(exec_reg, "get", return_value=FakeExec()), \
             patch.object(ws_manager, "broadcast", new=AsyncMock()), \
             patch.object(recovery, "update_session_status", new=AsyncMock()), \
             patch.object(wf, "check_and_advance", new=advance):
            await recovery._dispatch_recovery(payload)
        return advance

    async def test_recovery_advances_participant(self):
        import core.workflow as wf
        gid = 7002
        wf._orch.end(gid)
        wf.start(gid, [self._single(1, "A"), self._single(2, "B")])
        try:
            advance = await self._run_recovery(gid, bot_id=1, parent_id=None, full_text="干完了 完毕")
            advance.assert_awaited_once_with(gid, "干完了 完毕", 1)
        finally:
            wf._orch.end(gid)

    async def test_recovery_skips_non_participant(self):
        import core.workflow as wf
        gid = 7003
        wf._orch.end(gid)
        wf.start(gid, [self._single(1, "A"), self._single(2, "B")])
        try:
            # bot 2 不是当前阶段在岗者 → 不推进
            advance = await self._run_recovery(gid, bot_id=2, parent_id=None, full_text="完毕")
            advance.assert_not_awaited()
        finally:
            wf._orch.end(gid)

    async def test_recovery_skips_subagent(self):
        import core.workflow as wf
        gid = 7004
        wf._orch.end(gid)
        wf.start(gid, [self._single(1, "A"), self._single(2, "B")])
        try:
            # bot 1 是参与者，但这是子代理会话（parent_id 非空）→ 不推进
            advance = await self._run_recovery(gid, bot_id=1, parent_id="parent-1", full_text="完毕")
            advance.assert_not_awaited()
        finally:
            wf._orch.end(gid)


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
                await ctx.interaction.broadcast(ctx.group_id, {
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
             patch.object(runner, "global_db", return_value=fake_db_cm), \
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
             patch.object(runner, "global_db", return_value=fake_db_cm), \
             patch.object(runner.exec_registry, "get", return_value=FakeExec()):
            await runner.run_unit(1, unit, StubOrch())

        ctx = captured["ctx"]
        self.assertEqual(ctx.user_message, "请开始你的工作。")
        self.assertEqual(ctx.workflow_suffix, "[阶段指令]")
        from core.orchestration.interaction import StandardInteraction
        self.assertIsInstance(ctx.interaction, StandardInteraction)


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


class TestOrchestratorContractDefaults(unittest.TestCase):
    """ABC 默认实现：只实现 begin/observe 的最小编排器，门面依赖的查询/流转方法
    必须有安全默认（曾经这些方法只存在于 DeclarativeOrchestrator，第二实现一接入就崩）。"""

    def _bare(self):
        from core.orchestration.base import Orchestrator, OrchestratorStep

        class Bare(Orchestrator):
            orchestrator_id = "bare_test"

            def begin(self, group_id, spec):
                return OrchestratorStep()

            def observe(self, group_id, bot_id, response):
                return OrchestratorStep()

        return Bare()

    def test_defaults_are_safe(self):
        from core.orchestration.base import OrchestratorStep
        o = self._bare()
        self.assertIsNone(o.current_bot(1))
        self.assertIsNone(o.current_pool_bots(1))
        self.assertEqual(o.system_suffix(1), "")
        self.assertIsNone(o.serialize(1))
        self.assertEqual(o.resume_units(1), [])
        self.assertIsNone(o.end(1))  # no-op, must not raise
        step = o.advance(1)
        self.assertIsInstance(step, OrchestratorStep)
        self.assertFalse(step.done)


class TestRoundRobinOrchestrator(unittest.TestCase):
    """第二编排器（plugins/round_robin.py）独立验证编排契约：纯决策、可序列化、可恢复。"""

    def setUp(self):
        from core.orchestration.plugins.round_robin import RoundRobinOrchestrator
        self.orch = RoundRobinOrchestrator()
        self.bots = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]

    def test_begin_dispatches_first_speaker(self):
        step = self.orch.begin(10, {"bots": self.bots, "rounds": 2})
        self.assertTrue(step.broadcast_state)
        self.assertEqual(len(step.next_units), 1)
        self.assertEqual(step.next_units[0].bot["id"], 1)
        self.assertEqual(self.orch.current_bot(10)["id"], 1)

    def test_observe_rotates_and_wraps_rounds(self):
        self.orch.begin(11, {"bots": self.bots, "rounds": 2})
        s1 = self.orch.observe(11, 1, "r1 a")
        self.assertEqual(s1.next_units[0].bot["id"], 2)       # 轮到 B
        s2 = self.orch.observe(11, 2, "r1 b")                  # B 说完 → 第2轮 A
        self.assertEqual(s2.next_units[0].bot["id"], 1)
        snap = self.orch.snapshot(11)
        self.assertEqual(snap["round"], 2)

    def test_done_after_all_rounds(self):
        self.orch.begin(12, {"bots": self.bots, "rounds": 1})
        self.orch.observe(12, 1, "a")
        step = self.orch.observe(12, 2, "b")                   # 1 轮跑满
        self.assertTrue(step.done)
        self.assertEqual(self.orch.snapshot(12), {"active": False})  # end 已清状态

    def test_observe_ignores_wrong_bot(self):
        self.orch.begin(13, {"bots": self.bots, "rounds": 1})
        step = self.orch.observe(13, 2, "B 抢话")              # 当前应是 A
        self.assertEqual(step.next_units, [])
        self.assertEqual(self.orch.current_bot(13)["id"], 1)

    def test_serialize_restore_resume(self):
        self.orch.begin(14, {"bots": self.bots, "rounds": 3})
        self.orch.observe(14, 1, "a")                          # 游标到 B
        blob = self.orch.serialize(14)
        import json
        blob = json.loads(json.dumps(blob))                    # 模拟落库往返
        fresh = type(self.orch)()
        fresh.restore(14, blob)
        self.assertEqual(fresh.current_bot(14)["id"], 2)
        units = fresh.resume_units(14)
        self.assertEqual([u.bot["id"] for u in units], [2])


class TestOrchestratorPluggability(unittest.IsolatedAsyncioTestCase):
    """registry 发现第二编排器；门面按 group 路由到不同编排器且互相隔离。"""

    def test_registry_discovers_plugin(self):
        from core.orchestration import registry
        ids = registry.reload()
        self.assertIn("workflow_v1", ids)
        self.assertIn("round_robin_v1", ids)
        self.assertEqual(registry.get("does_not_exist").orchestrator_id, "workflow_v1")

    def test_facade_routes_per_group(self):
        import core.workflow as wf
        g_rr, g_wf = 7501, 7502
        wf._orch.end(g_wf)
        wf.end(g_rr)
        try:
            # round_robin 组：begin 通过门面 start（忽略返回的 step，仅建状态）
            wf.start(g_rr, {"bots": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "rounds": 1},
                     "round_robin_v1")
            # 默认 workflow_v1 组：单阶段
            wf.start(g_wf, [{"id": 9, "name": "Z", "avatar_color": "#111",
                             "stage_type": "single", "done_keyword": "完毕", "role": "Dev"}])

            self.assertEqual(wf.current_bot(g_rr)["id"], 1)     # 路由到 round_robin
            self.assertEqual(wf.current_bot(g_wf)["id"], 9)     # 路由到 declarative
            self.assertTrue(wf.is_workflow_participant(g_rr, 1))
            self.assertFalse(wf.is_workflow_participant(g_rr, 9))

            wf.end(g_rr)
            self.assertNotIn(g_rr, wf._group_orch)              # 绑定已清
            self.assertIsNone(wf.current_bot(g_rr))
            self.assertEqual(wf.current_bot(g_wf)["id"], 9)     # 另一组不受影响
        finally:
            wf.end(g_rr)
            wf._orch.end(g_wf)
            wf._group_orch.pop(g_wf, None)


class TestConfirmGateGlue(unittest.IsolatedAsyncioTestCase):
    """runner.apply_step 把 step.confirm_gate 落成一条带 meta 的内联确认卡片并广播。"""

    async def test_apply_step_posts_confirm_gate_card(self):
        from core import runner
        from core.orchestration.base import OrchestratorStep

        saved = {}
        async def fake_save(db, gid, mid, content, **kw):
            saved.update(gid=gid, mid=mid, content=content, meta=kw.get("meta"))
            return 77
        bcast = []
        async def cap(gid, payload):
            bcast.append(payload)

        fake_db_cm = MagicMock()
        fake_db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_db_cm.__aexit__ = AsyncMock(return_value=False)

        class StubOrch:
            def snapshot(self, gid): return {"active": True}
            def serialize(self, gid): return None

        step = OrchestratorStep(confirm_gate={
            "gate_id": "1-0", "label": "确认「需求」这一步已完成", "bot_id": 3, "stage_name": "需求",
        })
        with patch.object(runner, "save_message", new=fake_save), \
             patch.object(runner, "get_messages",
                          new=AsyncMock(return_value=[{"id": 77, "content": "确认「需求」这一步已完成",
                                                       "meta": {"kind": "confirm_gate", "gate_id": "1-0"}}])), \
             patch.object(runner, "write_connect", return_value=fake_db_cm), \
             patch.object(runner.bus, "broadcast", new=cap):
            await runner.apply_step(1, StubOrch(), step)

        # 落库的卡片：挂在 bot 名下，content=label，meta.kind=confirm_gate 带 gate_id
        self.assertEqual(saved["mid"], 3)
        self.assertEqual(saved["content"], "确认「需求」这一步已完成")
        self.assertEqual(saved["meta"]["kind"], "confirm_gate")
        self.assertEqual(saved["meta"]["gate_id"], "1-0")
        self.assertEqual(saved["meta"]["status"], "pending")
        # 广播了一条 message（带 meta）
        msgs = [b for b in bcast if b.get("type") == "message"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["meta"]["kind"], "confirm_gate")


class TestStartRdPipeline(unittest.IsolatedAsyncioTestCase):
    """worker 侧启动 RD 流水线：角色匹配 BA/Dev/QA，缺角色则提示不启动。"""

    def _cm(self):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=MagicMock())
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    async def test_missing_role_aborts_with_message(self):
        from runtime import dispatch
        members = [{"id": 1, "name": "BA", "type": "bot", "role": "BA", "avatar_color": "#1"}]
        bcast = []
        async def cap(gid, p): bcast.append(p)
        with patch.object(dispatch.db, "global_db", return_value=self._cm()), \
             patch.object(dispatch.db, "get_members", new=AsyncMock(return_value=members)), \
             patch.object(dispatch.bus, "broadcast", new=cap), \
             patch("core.workflow.apply", new=AsyncMock()) as mock_apply:
            await dispatch.dispatch_start_workflow({"group_id": 9001})
        self.assertTrue(any("缺少角色" in (p.get("content", "")) for p in bcast))
        mock_apply.assert_not_awaited()   # 没启动工作流

    async def test_all_roles_start_pipeline(self):
        from runtime import dispatch
        import core.workflow as wf
        gid = 9002
        wf.end(gid)
        members = [
            {"id": 1, "name": "小需", "type": "bot", "role": "需求分析", "avatar_color": "#1"},
            {"id": 2, "name": "小开", "type": "bot", "role": "后端开发", "avatar_color": "#2"},
            {"id": 3, "name": "小测", "type": "bot", "role": "测试", "avatar_color": "#3"},
        ]
        bcast = []
        async def cap(g, p): bcast.append(p)
        try:
            with patch.object(dispatch.db, "global_db", return_value=self._cm()), \
                 patch.object(dispatch.db, "get_members", new=AsyncMock(return_value=members)), \
                 patch.object(dispatch.bus, "broadcast", new=cap), \
                 patch("core.workflow.apply", new=AsyncMock()):
                await dispatch.dispatch_start_workflow({"group_id": gid})
            # 工作流已登记：3 个阶段（BA→Dev→QA），首阶段当前 bot 是 BA(小需)
            snap = wf._snapshot(gid)
            self.assertEqual(len(snap["stages"]), 3)
            self.assertEqual(wf.current_bot(gid)["id"], 1)
            self.assertTrue(any("需求流程已开始" in p.get("content", "") for p in bcast))
        finally:
            wf.end(gid)

    async def test_user_driven_first_stage_carries_workflow_suffix(self):
        """首阶段(BA)由用户驱动、走 orch.dispatch —— 必须把 system_suffix（含哨兵指令）
        挂到 WorkUnit.prompt_suffix 上，否则 BA 收不到“输出 [[BA_DONE]]”的指令，
        确认门永远挂不起来（这正是 Dev 不开工的根因）。"""
        from core.orchestration.declarative import DeclarativeOrchestrator
        from core.orchestration.pipeline import build_rd_pipeline
        orch = DeclarativeOrchestrator()
        ba = {"id": 1, "name": "BA", "avatar_color": "#1", "role": "BA"}
        dev = {"id": 2, "name": "Dev", "avatar_color": "#2", "role": "Dev"}
        qa = {"id": 3, "name": "QA", "avatar_color": "#3", "role": "QA"}
        orch.begin(7, build_rd_pipeline(ba, dev, qa))
        members = [{**ba, "type": "bot"}, {**dev, "type": "bot"}, {**qa, "type": "bot"}]
        step = await orch.dispatch(7, {"content": "做个计算器"}, members, [])
        self.assertTrue(step.next_units)
        unit = step.next_units[0]
        self.assertEqual(unit.bot["id"], 1)                     # BA 应答
        self.assertIn("[[BA_DONE]]", unit.prompt_suffix)        # 哨兵指令必须随单元下发
        self.assertIn("Jira", unit.prompt_suffix)               # 建工单指令也在


class TestSnapshotFromPersistedState(unittest.TestCase):
    """跨进程修复：REST 应用跑在主进程，其编排器内存恒为空。它必须能仅凭持久化的
    state blob（从 group 私有库读出）渲染工作流快照，且不污染任何共享内存状态。"""

    def _orch(self):
        from core.orchestration.declarative import DeclarativeOrchestrator
        return DeclarativeOrchestrator()

    def _gated(self, bid, name, keyword="[[DONE]]"):
        return {"id": bid, "name": name, "avatar_color": "#111",
                "stage_type": "single", "done_keyword": keyword, "role": "Dev",
                "gate": True}

    def test_snapshot_state_renders_active_blob_without_mutation(self):
        live = self._orch()
        live.begin(7, [self._gated(1, "BA"), self._gated(2, "Dev")])
        live.observe(7, 1, "all done [[DONE]]")     # BA 吐哨兵 → 挂起确认门
        blob = live.serialize(7)
        self.assertIn("awaiting_confirm", blob)

        fresh = self._orch()                         # = 主进程：内存空
        self.assertEqual(fresh.snapshot(7), {"active": False})

        snap = fresh.snapshot_state(blob)
        self.assertTrue(snap["active"])
        self.assertEqual(snap["current"], 0)
        self.assertEqual(len(snap["stages"]), 2)
        self.assertEqual(snap["awaiting_confirm"], "7-0")
        # 纯函数：绝不能把状态塞进单例 _state
        self.assertIsNone(fresh.get(7))
        self.assertEqual(fresh.snapshot(7), {"active": False})

    def test_snapshot_state_empty_blob_inactive(self):
        fresh = self._orch()
        self.assertEqual(fresh.snapshot_state(None), {"active": False})
        self.assertEqual(fresh.snapshot_state({}), {"active": False})

    def test_live_snapshot_matches_snapshot_state(self):
        live = self._orch()
        live.begin(7, [self._gated(1, "BA"), self._gated(2, "Dev")])
        live.observe(7, 1, "done [[DONE]]")
        self.assertEqual(live.snapshot(7), live.snapshot_state(live.serialize(7)))


class TestWorkflowMutationRouting(unittest.IsolatedAsyncioTestCase):
    """工作流的 next/end 是状态变更，必须在 worker 进程对活内存编排器执行；REST 端点
    （主进程）只能转发控制帧，绝不能在本进程直接 advance/end（空内存 no-op + 写错库）。"""

    async def test_next_endpoint_forwards_frame_to_worker(self):
        from runtime.ipc import protocol
        self.assertIn(protocol.WORKFLOW_NEXT, protocol.DOWNSTREAM)
        import api.workflow as apiwf
        sup = MagicMock()
        sup.send_to_worker = AsyncMock()
        with patch.object(apiwf.sup_mod, "supervisor", sup):
            await apiwf.next_workflow(42)
        sup.send_to_worker.assert_awaited_once()
        gid, frame = sup.send_to_worker.await_args.args
        self.assertEqual(gid, 42)
        self.assertEqual(frame["type"], protocol.WORKFLOW_NEXT)
        self.assertEqual(frame["group_id"], 42)

    async def test_end_endpoint_forwards_frame_to_worker(self):
        from runtime.ipc import protocol
        self.assertIn(protocol.WORKFLOW_END, protocol.DOWNSTREAM)
        import api.workflow as apiwf
        sup = MagicMock()
        sup.send_to_worker = AsyncMock()
        with patch.object(apiwf.sup_mod, "supervisor", sup):
            await apiwf.end_workflow(42)
        gid, frame = sup.send_to_worker.await_args.args
        self.assertEqual(gid, 42)
        self.assertEqual(frame["type"], protocol.WORKFLOW_END)

    async def test_dispatch_next_advances_in_worker(self):
        import core.workflow as wf
        from runtime.dispatch import dispatch_workflow_next
        with patch.object(wf, "advance", new=AsyncMock()) as adv:
            await dispatch_workflow_next({"group_id": 7})
        adv.assert_awaited_once_with(7)

    async def test_dispatch_end_clears_state_and_broadcasts(self):
        import core.workflow as wf
        from core import workflow_store
        import runtime.dispatch as dsp
        with patch.object(wf, "end") as end, \
             patch.object(workflow_store, "clear_state", new=AsyncMock()) as clear, \
             patch.object(dsp.bus, "broadcast", new=AsyncMock()) as bcast:
            await dsp.dispatch_workflow_end({"group_id": 7})
        end.assert_called_once_with(7)
        clear.assert_awaited_once_with(7)
        bcast.assert_awaited_once()
        gid, payload = bcast.await_args.args
        self.assertEqual(gid, 7)
        self.assertEqual(payload, {"type": "workflow_update", "active": False})


if __name__ == "__main__":
    unittest.main()
