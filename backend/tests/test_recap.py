import sys
import os
import shutil
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
from core.recap import generate_and_cache_recap, clear_recap, generate_personal_recap
from main import app
from httpx import AsyncClient

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_recap_chat.db")

class TestAwaySummaryRecap(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Clean up database
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

        self._orig_db_path = database.DB_PATH   # 还原全局，避免污染后续测试文件
        database.DB_PATH = TEST_DB_PATH

        # Reset module-level state for recap generator to prevent test leakage
        from core.recap.generator import _last_generated, _generating_groups
        _last_generated.clear()
        _generating_groups.clear()

        # Set up schema and run migrations
        await database.init_db()

        # Patch bus broadcast and others
        self.patcher_bus = patch("bus.bus.broadcast", new_callable=AsyncMock)
        self.mock_broadcast = self.patcher_bus.start()

        # Seed test group and messages
        async with database.get_db() as db_conn:
            await db_conn.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db_conn.execute(
                "INSERT INTO members (id, group_id, name, type, role, model_provider, model_name) "
                "VALUES (10, 1, 'DevBot', 'bot', 'Developer', 'deepseek', 'deepseek-chat')"
            )
            await db_conn.execute(
                "INSERT INTO messages (id, group_id, member_id, content, sender_name, sender_type) "
                "VALUES (100, 1, 10, 'Hello from bot', 'DevBot', 'bot')"
            )
            await db_conn.commit()

    async def asyncTearDown(self):
        self.patcher_bus.stop()
        database.DB_PATH = self._orig_db_path
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_generate_and_cache_recap_success(self, mock_call_ai):
        mock_call_ai.return_value = {"content": "This is a recap summary."}

        summary = await generate_and_cache_recap(1)

        self.assertEqual(summary, "This is a recap summary.")
        mock_call_ai.assert_called_once()
        self.mock_broadcast.assert_called_with(1, {
            "type": "recap_updated",
            "group_id": 1,
            "away_summary": "This is a recap summary."
        })

        # Verify cached value in db
        async with database.get_db() as db_conn:
            group = await database.get_group(db_conn, 1)
            self.assertEqual(group["away_summary"], "This is a recap summary.")

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_generate_and_cache_recap_empty_messages(self, mock_call_ai):
        # Delete messages first
        async with database.get_db() as db_conn:
            await db_conn.execute("DELETE FROM messages WHERE group_id = 1")
            await db_conn.commit()

        summary = await generate_and_cache_recap(1)
        self.assertIsNone(summary)
        mock_call_ai.assert_not_called()

    async def test_clear_recap(self):
        # Set recap first
        async with database.get_db() as db_conn:
            await db_conn.execute("UPDATE groups SET away_summary = 'Old Recap' WHERE id = 1")
            await db_conn.commit()

        await clear_recap(1)

        # Verify cleared in db
        async with database.get_db() as db_conn:
            group = await database.get_group(db_conn, 1)
            self.assertIsNone(group["away_summary"])

        self.mock_broadcast.assert_called_with(1, {
            "type": "recap_updated",
            "group_id": 1,
            "away_summary": None
        })

    def test_schema_consistency_groups_and_messages(self):
        """Verify that the groups table in CENTRAL_DDL has away_summary column,
        and messages table in GROUP_DDL has meta and other expected columns."""
        from db.schema_split import _CENTRAL_DDL, _GROUP_DDL
        
        # Check central DDL groups table contains away_summary
        groups_ddl = next((ddl for ddl in _CENTRAL_DDL if "CREATE TABLE" in ddl and "groups" in ddl), None)
        self.assertIsNotNone(groups_ddl)
        self.assertIn("away_summary", groups_ddl)
        
        # Check group DDL messages table contains meta
        messages_ddl = next((ddl for ddl in _GROUP_DDL if "CREATE TABLE" in ddl and "messages" in ddl), None)
        self.assertIsNotNone(messages_ddl)
        self.assertIn("meta", messages_ddl)

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_recap_debounce_and_deduplication(self, mock_call_ai):
        mock_call_ai.return_value = {"content": "Debounced summary."}
        
        # First call: generates recap
        summary1 = await generate_and_cache_recap(1)
        self.assertEqual(summary1, "Debounced summary.")
        self.assertEqual(mock_call_ai.call_count, 1)
        
        # Second call immediately after: debounced (returns None)
        summary2 = await generate_and_cache_recap(1)
        self.assertIsNone(summary2)
        self.assertEqual(mock_call_ai.call_count, 1)
        
        # Reset debounce state to allow generating again
        from core.recap.generator import _last_generated, _generating_groups
        _last_generated.clear()
        _generating_groups.clear()
        
        # Third call: runs again
        summary3 = await generate_and_cache_recap(1)
        self.assertEqual(summary3, "Debounced summary.")
        self.assertEqual(mock_call_ai.call_count, 2)

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_personal_recap_summarizes_unread(self, mock_call_ai):
        """方案 1：概括成员 last_read_id 之后错过的消息，返回未读数 + 摘要。"""
        mock_call_ai.return_value = {"content": "你错过了 Dev 的提交。"}
        async with database.get_db() as db_conn:
            # 人类成员 20 已读到消息 100；之后有两条未读
            await db_conn.execute("INSERT INTO member_read (member_id, group_id, last_read_id) VALUES (20, 1, 100)")
            await db_conn.execute("INSERT INTO messages (id, group_id, member_id, content, sender_name, sender_type) VALUES (101,1,10,'fix done','DevBot','bot')")
            await db_conn.execute("INSERT INTO messages (id, group_id, member_id, content, sender_name, sender_type) VALUES (102,1,10,'tests pass','DevBot','bot')")
            await db_conn.commit()
        res = await generate_personal_recap(1, 20)
        self.assertEqual(res["unread_count"], 2)
        self.assertEqual(res["summary"], "你错过了 Dev 的提交。")
        mock_call_ai.assert_called_once()

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_personal_recap_no_unread(self, mock_call_ai):
        """已读到最新 → 无未读 → 不调用 LLM、summary 为空。"""
        async with database.get_db() as db_conn:
            await db_conn.execute("INSERT INTO member_read (member_id, group_id, last_read_id) VALUES (21, 1, 100)")
            await db_conn.commit()
        res = await generate_personal_recap(1, 21)
        self.assertEqual(res, {"unread_count": 0, "summary": None, "covered_through_id": 0})
        mock_call_ai.assert_not_called()

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_personal_recap_suppressed_after_ack(self, mock_call_ai):
        """点 ✕ (ack) 后，同一批未读不再生成摘要 —— 即使消息仍未读、用户重连/切群也不再弹。"""
        from core.recap import ack_personal_recap
        mock_call_ai.return_value = {"content": "你错过了 Dev 的提交。"}
        # member 22 尚未确认，消息 100 未读 → 先会出摘要
        first = await generate_personal_recap(1, 22)
        self.assertIsNotNone(first["summary"])
        # 点 ✕：把「该用户」的 recap 水位线推进到当前最新消息
        await ack_personal_recap(1, 22)
        # 同一批未读 → 不再出摘要、不再调 LLM
        mock_call_ai.reset_mock()
        after = await generate_personal_recap(1, 22)
        self.assertEqual(after["unread_count"], 0)
        self.assertIsNone(after["summary"])
        mock_call_ai.assert_not_called()

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_personal_recap_reappears_on_new_activity_after_ack(self, mock_call_ai):
        """ack 之后若有「全新」活动，仍会再弹一条只覆盖新活动的摘要。"""
        from core.recap import ack_personal_recap
        mock_call_ai.return_value = {"content": "Dev 又提交了。"}
        await ack_personal_recap(1, 23)   # 确认到当前最新 (msg 100)
        async with database.get_db() as db_conn:
            await db_conn.execute(
                "INSERT INTO messages (id, group_id, member_id, content, sender_name, sender_type) "
                "VALUES (110,1,10,'new commit','DevBot','bot')"
            )
            await db_conn.commit()
        res = await generate_personal_recap(1, 23)
        self.assertEqual(res["unread_count"], 1)
        self.assertEqual(res["summary"], "Dev 又提交了。")

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_ack_personal_recap_prevents_toctou(self, mock_call_ai):
        """防止 TOCTOU (P1): ✕ 只确认横幅生成时的消息，在生成横幅到点击 ✕ 之间来的新消息不应被确认。"""
        from core.recap import ack_personal_recap
        mock_call_ai.return_value = {"content": "这是 100 以前的摘要"}
        
        # 1. 生成 recap，此时只包含到 ID 100 消息
        res = await generate_personal_recap(1, 23)
        self.assertEqual(res["covered_through_id"], 100)
        
        # 2. 模拟 TOCTOU 窗口：在点击 ✕ 之前，队友发了新消息 105
        async with database.get_db() as db_conn:
            await db_conn.execute(
                "INSERT INTO messages (id, group_id, member_id, content, sender_name, sender_type) "
                "VALUES (105, 1, 10, 'TOCTOU message', 'UserB', 'human')"
            )
            await db_conn.commit()
            
        # 3. 点击 ✕，回传当时横幅对应的 covered_through_id = 100
        await ack_personal_recap(1, 23, res["covered_through_id"])
        
        # 4. 再次获取 recap，新来的消息 105 必须再次弹 recap 汇总，不能被吞掉
        mock_call_ai.reset_mock()
        mock_call_ai.return_value = {"content": "这是 105 的新摘要"}
        after = await generate_personal_recap(1, 23)
        self.assertEqual(after["unread_count"], 1)
        self.assertEqual(after["summary"], "这是 105 的新摘要")

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_ack_personal_recap_is_per_user(self, mock_call_ai):
        """✕ 只清自己的：member 24 确认后，member 25 仍能看到同一批未读的摘要。"""
        from core.recap import ack_personal_recap
        mock_call_ai.return_value = {"content": "进度摘要。"}
        await ack_personal_recap(1, 24)
        res_other = await generate_personal_recap(1, 25)
        self.assertEqual(res_other["unread_count"], 1)
        self.assertIsNotNone(res_other["summary"])

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_force_bypasses_debounce(self, mock_call_ai):
        """force=True 用于用户手动触发：跳过 5s 去抖，必定重算（不被静默跳过）。"""
        mock_call_ai.return_value = {"content": "Forced summary."}

        self.assertEqual(await generate_and_cache_recap(1), "Forced summary.")
        self.assertEqual(mock_call_ai.call_count, 1)

        # 紧接着的 eager 调用：被去抖跳过
        self.assertIsNone(await generate_and_cache_recap(1))
        self.assertEqual(mock_call_ai.call_count, 1)

        # 紧接着的 force 调用：绕过去抖，重新生成
        self.assertEqual(await generate_and_cache_recap(1, force=True), "Forced summary.")
        self.assertEqual(mock_call_ai.call_count, 2)

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    @patch("workspace.layout.get_group_language")
    async def test_recap_i18n_english(self, mock_get_lang, mock_call_ai):
        mock_get_lang.return_value = "en"
        mock_call_ai.return_value = {"content": "English recap."}
        
        summary = await generate_and_cache_recap(1, force=True)
        self.assertEqual(summary, "English recap.")
        
        mock_call_ai.assert_called_once()
        kwargs = mock_call_ai.call_args.kwargs
        self.assertIn("You are a project collaboration assistant.", kwargs["system_prompt"])
        self.assertIn("Here are the recent chat logs", kwargs["messages"][0]["content"])

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    @patch("workspace.layout.get_group_language")
    async def test_recap_i18n_default_chinese(self, mock_get_lang, mock_call_ai):
        mock_get_lang.return_value = "zh"
        mock_call_ai.return_value = {"content": "中文摘要。"}
        
        summary = await generate_and_cache_recap(1, force=True)
        self.assertEqual(summary, "中文摘要。")
        
        mock_call_ai.assert_called_once()
        kwargs = mock_call_ai.call_args.kwargs
        self.assertIn("你是一个项目协作助手。", kwargs["system_prompt"])
        self.assertIn("以下是项目协作中最近的聊天记录", kwargs["messages"][0]["content"])

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_recap_with_git_and_jira_context(self, mock_call_ai):
        from pathlib import Path
        mock_call_ai.return_value = {"content": "Recap with git and jira."}
        
        # 1. Seed a JIRA ticket
        async with database.get_db() as db_conn:
            await db_conn.execute(
                "INSERT INTO tickets (ticket_id, group_id, title, status, project) "
                "VALUES ('DFT-101', 1, 'Build Pac-Man Game', 'in_progress', 'GameProj')"
            )
            await db_conn.commit()
            
        # 2. Mock Git CLI execution
        mock_proc_log = MagicMock()
        mock_proc_log.communicate = AsyncMock(return_value=(b"feat: initialize board", b""))
        mock_proc_status = MagicMock()
        mock_proc_status.communicate = AsyncMock(return_value=(b"M src/index.js", b""))
        
        async def mock_exec(*args, **kwargs):
            if "log" in args:
                return mock_proc_log
            if "status" in args:
                return mock_proc_status
            return MagicMock()
            
        with patch("workspace.layout.group_shared_dir", return_value=Path("/tmp/mock_ws")), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            
            summary = await generate_and_cache_recap(1, force=True)
            self.assertEqual(summary, "Recap with git and jira.")
            
            mock_call_ai.assert_called_once()
            kwargs = mock_call_ai.call_args.kwargs
            prompt_content = kwargs["messages"][0]["content"]
            
            # Check Jira info presence
            self.assertIn("DFT-101: Build Pac-Man Game (状态: in_progress, 项目: GameProj)", prompt_content)
            # Check Git info presence
            self.assertIn("feat: initialize board", prompt_content)
            self.assertIn("M src/index.js", prompt_content)



class TestRecapApi(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}

        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

        self._orig_db_path = database.DB_PATH   # 还原全局，避免污染后续测试文件
        database.DB_PATH = TEST_DB_PATH
        await database.init_db()

        async with database.get_db() as db_conn:
            await db_conn.execute("INSERT INTO groups (id, name, away_summary) VALUES (1, 'Test Group', 'Cached Recap')")
            await db_conn.commit()

    async def asyncTearDown(self):
        from core import auth as _auth
        app.dependency_overrides.pop(_auth.get_current_user, None)
        database.DB_PATH = self._orig_db_path

        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    async def test_get_recap_success(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/api/groups/1/recap")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"group_id": 1, "away_summary": "Cached Recap"})

    async def test_get_recap_not_found(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/api/groups/999/recap")
            self.assertEqual(resp.status_code, 404)

    @patch("core.recap.clear_recap", new_callable=AsyncMock)
    async def test_delete_recap(self, mock_clear_recap):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.delete("/api/groups/1/recap")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"ok": True})
            mock_clear_recap.assert_called_once_with(1)

    @patch("core.recap.generate_and_cache_recap", new_callable=AsyncMock)
    async def test_trigger_recap(self, mock_generate_recap):
        mock_generate_recap.return_value = "Triggered Summary"
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.post("/api/groups/1/recap/trigger")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"ok": True, "away_summary": "Triggered Summary"})
            mock_generate_recap.assert_called_once_with(1, force=True)

    @patch("core.recap.generate_personal_recap", new_callable=AsyncMock)
    async def test_personal_recap_endpoint(self, mock_gen):
        mock_gen.return_value = {"unread_count": 3, "summary": "你错过了 3 条。"}
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/api/groups/1/recap/personal/20")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"unread_count": 3, "summary": "你错过了 3 条。"})
            mock_gen.assert_awaited_once_with(1, 20)

    @patch("core.recap.ack_personal_recap", new_callable=AsyncMock)
    async def test_ack_recap_endpoint(self, mock_ack):
        """POST .../recap/ack/{member_id} → 记录该成员已看过，返回推进后的水位线。"""
        mock_ack.return_value = 142
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.post("/api/groups/1/recap/ack/20")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"ok": True, "acked_through": 142})
            mock_ack.assert_awaited_once_with(1, 20, None)

    @patch("core.recap.generate_and_cache_recap", new_callable=AsyncMock)
    async def test_trigger_recap_falls_back_to_cache_when_skipped(self, mock_generate_recap):
        # 生成被去抖/在途跳过返回 None 时，端点回退到现有缓存，绝不把 banner 清空。
        mock_generate_recap.return_value = None
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.post("/api/groups/1/recap/trigger")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"ok": True, "away_summary": "Cached Recap"})


class TestDebouncePrune(unittest.TestCase):
    """_last_generated 不能随群数无界增长：超过 TTL 的去抖时间戳要被清掉。"""

    def test_prune_removes_stale_keeps_recent(self):
        from core.recap import generator as g
        g._last_generated.clear()
        try:
            g._last_generated[1] = 1000.0        # 远古
            g._last_generated[2] = 100_000.0     # "现在"
            g._prune_last_generated(now=100_000.0)
            self.assertNotIn(1, g._last_generated)
            self.assertIn(2, g._last_generated)
        finally:
            g._last_generated.clear()


def _close_spawned(mock_spawn):
    """关闭被 fire-and-forget 的协程，避免 'coroutine never awaited' 噪音。"""
    for c in mock_spawn.call_args_list:
        for a in c.args:
            if asyncio.iscoroutine(a):
                a.close()


class TestRecapEventTrigger(unittest.IsolatedAsyncioTestCase):
    """解耦契约：apply_step 在 gate/done 时 publish WorkflowPaused（而非直接调 recap）；
    worker._recap_on_paused 只为本 worker 持有的活跃 group 生成。"""

    async def _run_apply_step(self, step):
        from core import runner
        from bus.events import WorkflowPaused
        with patch.object(runner, "bus") as bus, \
             patch.object(runner, "workflow_store") as ws, \
             patch.object(runner, "bg") as bg_mock, \
             patch.object(runner, "_post_confirm_gate", new=AsyncMock()):
            bus.publish = AsyncMock()
            bus.broadcast = AsyncMock()
            ws.save_state = AsyncMock()
            ws.clear_state = AsyncMock()
            orch = MagicMock()
            orch.snapshot.return_value = {"active": True}
            orch.serialize.return_value = None
            await runner.apply_step(1, orch, step)
            _close_spawned(bg_mock.spawn)
            _close_spawned(bg_mock.spawn_group)
            published = [c.args[0] for c in bus.publish.call_args_list if c.args]
        return [p for p in published if isinstance(p, WorkflowPaused)]

    async def test_gate_publishes_workflow_paused(self):
        from core.orchestration.base import OrchestratorStep
        paused = await self._run_apply_step(OrchestratorStep(
            confirm_gate={"gate_id": "1-0", "label": "x", "bot_id": 1, "stage_name": "BA"}))
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0].reason, "gate")

    async def test_done_publishes_workflow_paused(self):
        from core.orchestration.base import OrchestratorStep
        paused = await self._run_apply_step(OrchestratorStep(done=True))
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0].reason, "done")

    async def test_plain_advance_does_not_publish(self):
        from core.orchestration.base import OrchestratorStep, WorkUnit
        paused = await self._run_apply_step(OrchestratorStep(
            next_units=[WorkUnit(bot={"id": 2, "name": "Dev"})]))
        self.assertEqual(paused, [])

    async def test_recap_on_paused_skips_inactive_group(self):
        from runtime.worker import Worker
        from runtime.lifecycle import manager as lifecycle
        w = Worker.__new__(Worker)
        w.worker_id = "t"
        with patch.object(lifecycle, "is_active", return_value=False), \
             patch("core.recap.generate_and_cache_recap", new=AsyncMock()) as gen:
            await w._recap_on_paused(1)
        gen.assert_not_awaited()

    async def test_recap_on_paused_generates_for_active_group(self):
        import db as _db
        from runtime.worker import Worker
        from runtime.lifecycle import manager as lifecycle
        w = Worker.__new__(Worker)
        w.worker_id = "t"
        cm = MagicMock()
        cm.__enter__ = MagicMock()
        cm.__exit__ = MagicMock(return_value=False)
        with patch.object(lifecycle, "is_active", return_value=True), \
             patch.object(_db, "bind_db", return_value=cm), \
             patch("core.recap.generate_and_cache_recap", new=AsyncMock()) as gen:
            await w._recap_on_paused(7)
        gen.assert_awaited_once_with(7)


class TestRetrospective(unittest.IsolatedAsyncioTestCase):
    """Test retrospective memory logic, including event dispatch and file generation."""

    async def test_pump_recap_routes_done_to_retro(self):
        from runtime.worker import Worker
        from bus.events import WorkflowPaused

        w = Worker.__new__(Worker)
        w.bus = MagicMock()
        w._retro_on_done = MagicMock(return_value="retro_coro")
        w._recap_on_paused = MagicMock(return_value="recap_coro")

        evs = [
            WorkflowPaused(group_id=1, reason="done"),
            WorkflowPaused(group_id=1, reason="gate"),
        ]

        class MockSub:
            async def __aenter__(self): return self
            async def __aexit__(self, exc_type, exc_val, exc_tb): pass
            def __aiter__(self):
                self.idx = 0
                return self
            async def __anext__(self):
                if self.idx < len(evs):
                    ev = evs[self.idx]
                    self.idx += 1
                    return ev
                raise StopAsyncIteration

        w.bus.subscribe.return_value = MockSub()

        from core import bg
        with patch.object(bg, "spawn") as mock_spawn:
            await w._pump_recap()
            self.assertEqual(mock_spawn.call_count, 2)
            w._retro_on_done.assert_called_once_with(1)
            w._recap_on_paused.assert_called_once_with(1)

    async def test_retro_on_done_generates_for_active_group(self):
        import db as _db
        from runtime.worker import Worker
        from runtime.lifecycle import manager as lifecycle

        w = Worker.__new__(Worker)
        w.worker_id = "test_worker"

        cm = MagicMock()
        cm.__enter__ = MagicMock()
        cm.__exit__ = MagicMock(return_value=False)

        with patch.object(lifecycle, "is_active", return_value=True), \
             patch.object(_db, "bind_db", return_value=cm), \
             patch("core.recap.retro.generate_ticket_retrospective", new=AsyncMock()) as gen:
            await w._retro_on_done(5)
        gen.assert_awaited_once_with(5)

    async def test_generate_ticket_retrospective(self):
        import tempfile
        import shutil
        from pathlib import Path

        temp_dir = tempfile.mkdtemp()
        try:
            ws_mock = Path(temp_dir) / "group_1" / "shared"
            ws_mock.mkdir(parents=True)

            # Mock workspace
            with patch("core.recap.retro.group_workspace", return_value=ws_mock), \
                 patch("core.recap.retro.get_db") as mock_gdb, \
                 patch("core.recap.retro.global_db") as mock_cdb, \
                 patch("core.recap.retro.get_messages") as mock_get_msgs, \
                 patch("core.recap.retro.get_members") as mock_get_members, \
                 patch("core.recap.retro.call_ai_once") as mock_call_ai, \
                 patch("core.recap.retro.write_connect") as mock_write_conn, \
                 patch("core.recap.retro.bus.broadcast") as mock_broadcast:

                # Setup messages
                mock_get_msgs.return_value = [
                    {"id": 10, "content": "Hello", "sender_name": "BA", "is_deleted": False, "is_auto_reply": False},
                    {"id": 11, "content": "Code written", "sender_name": "Dev", "is_deleted": False, "is_auto_reply": False},
                ]
                mock_get_members.return_value = []

                # Setup LLM response
                mock_call_ai.return_value = {
                    "content": """
## 1. 最终成果摘要 (Executive Summary)
完成本轮开发，生成了测试组件。

## 2. 核心技术决策与动因 (Key Decisions & Rationale)
无

## 3. 避坑指南与教训 (Lessons & Anti-Patterns)
无

## 4. 技术债与后续待办 (Tech Debt & Next Steps)
无
"""
                }

                # Mock DB connection
                conn_mock = AsyncMock()
                mock_write_conn.return_value.__aenter__.return_value = conn_mock

                from core.recap.retro import generate_ticket_retrospective
                content = await generate_ticket_retrospective(1)

                # Verify retrospective returned content
                self.assertIsNotNone(content)
                self.assertIn("完成本轮开发", content)

                # Verify files created
                latest_path = ws_mock / "RETRO_LATEST.md"
                run_path = ws_mock / "retros" / "run_0.md"
                self.assertTrue(latest_path.exists())
                self.assertTrue(run_path.exists())
                self.assertIn("完成本轮开发", latest_path.read_text(encoding="utf-8"))

                # Verify anchor file created outside shared/
                anchor_path = ws_mock.parent / ".system" / "retro_anchor.json"
                self.assertTrue(anchor_path.exists())

                # Verify central DB update called
                conn_mock.execute.assert_called_once()
                sql = conn_mock.execute.call_args[0][0]
                args = conn_mock.execute.call_args[0][1]
                self.assertIn("UPDATE groups SET away_summary", sql)
                self.assertEqual(args[0], "完成本轮开发，生成了测试组件。")
                self.assertEqual(args[1], 1)

                # Verify broadcast called
                mock_broadcast.assert_called_once_with(1, {
                    "type": "recap_updated",
                    "group_id": 1,
                    "away_summary": "完成本轮开发，生成了测试组件。"
                })

        finally:
            shutil.rmtree(temp_dir)


class TestRecapHydrationAndOperationalError(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        # Create temp dir for WORKSPACE_ROOT
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_patchers = [
            patch("skills.constants.WORKSPACE_ROOT", Path(self.temp_dir))
        ]
        for p in self.workspace_patchers:
            p.start()

        # Set up Auth dependency override
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}

        # Use test central DB path
        self.test_central_db = os.path.join(self.temp_dir, "central.db")
        self._orig_db_path = database.DB_PATH
        database.DB_PATH = self.test_central_db
        await database.init_db()

        # Insert a test group in the central DB
        async with database.get_db() as db_conn:
            await db_conn.execute("INSERT INTO groups (id, name) VALUES (999, 'Hydration Group')")
            await db_conn.commit()

    async def asyncTearDown(self):
        from core import auth as _auth
        app.dependency_overrides.pop(_auth.get_current_user, None)
        database.DB_PATH = self._orig_db_path
        for p in self.workspace_patchers:
            p.stop()
        shutil.rmtree(self.temp_dir)

    async def test_endpoint_auto_hydrates_sleeping_group(self):
        # The database file for group 999 does not exist yet
        from runtime.dbpaths import group_db_path
        gdb_path = group_db_path(999)
        self.assertFalse(os.path.exists(gdb_path))

        # Accessing the recap personal endpoint should auto-hydrate/migrate the database
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/api/groups/999/recap/personal/1")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            # It succeeds with an empty recap response since there are no messages
            self.assertEqual(data["unread_count"], 0)
            self.assertIsNone(data["summary"])

        # Check that the group DB was created, schema initialized, and migrated
        self.assertTrue(os.path.exists(gdb_path))
        async with database.connect(gdb_path) as conn:
            # Check a table exists
            cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='member_read'")
            self.assertIsNotNone(await cur.fetchone())
            # Check a column from migration exists (last_recap_ack_id)
            cur = await conn.execute("PRAGMA table_info(member_read)")
            cols = [row[1] for row in await cur.fetchall()]
            self.assertIn("last_recap_ack_id", cols)

    async def test_operational_error_propagation(self):
        import sqlite3
        from core.recap.generator import generate_personal_recap, ack_personal_recap
        from ai.memory import maybe_summarize

        # 1. generate_personal_recap raises when there is a schema error
        with patch("core.recap.generator.get_db") as mock_get_db:
            mock_conn = AsyncMock()
            mock_conn.execute.side_effect = sqlite3.OperationalError("no such column: last_recap_ack_id")
            mock_get_db.return_value.__aenter__.return_value = mock_conn
            with self.assertRaises(sqlite3.OperationalError):
                await generate_personal_recap(1, 2)

        # 2. ack_personal_recap raises when there is a schema error
        with patch("core.recap.generator.write_connect") as mock_write_connect:
            mock_conn = AsyncMock()
            mock_conn.execute.side_effect = sqlite3.OperationalError("no such column: last_recap_ack_id")
            mock_write_connect.return_value.__aenter__.return_value = mock_conn
            with self.assertRaises(sqlite3.OperationalError):
                await ack_personal_recap(1, 2, covered_through_id=100)

        # 3. maybe_summarize raises when there is a schema error
        with patch("ai.memory._memory_db") as mock_memory_db:
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = sqlite3.OperationalError("no such column: thread_id")
            async_cm = AsyncMock()
            async_cm.__aenter__.return_value = mock_conn
            mock_memory_db.return_value = async_cm
            with self.assertRaises(sqlite3.OperationalError):
                await maybe_summarize(1, 2, "role", [3])


if __name__ == "__main__":
    unittest.main()
