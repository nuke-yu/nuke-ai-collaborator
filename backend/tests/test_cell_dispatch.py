"""CELL-14: worker real dispatch over the split data layer.

Proves a bot actually runs inside the cell topology: dispatch_user_message reads
the sender/members from the CENTRAL db, persists the user message + the bot reply
to the bound GROUP db, with only the AI seam (and noisy side effects) mocked.
Plus a light check that the entry launcher wires the real dispatch.
"""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import db.writer as _writer
from runtime.dispatch import dispatch_user_message, dispatch_wake_trigger

BOT_ID, HUMAN_ID, GID = 1, 2, 1


class TestCellDispatch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.central = os.path.join(self.tmp, "central.db")
        self.group = os.path.join(self.tmp, "group.db")
        self._orig_db, self._orig_w = db.DB_PATH, _writer.DB_PATH
        db.DB_PATH = self.central          # global_db() -> central
        await db.init_central_db(self.central)
        await db.init_group_db(self.group)
        # Seed central via a synchronous connection (avoids aiosqlite daemon-thread
        # connection churn that flakes under pytest's IsolatedAsyncioTestCase).
        with db.connect_sync(self.central) as c:
            c.execute("INSERT INTO groups (id, name) VALUES (?, 'proj')", (GID,))
            c.executemany(
                "INSERT INTO members (id, group_id, name, type, role) VALUES (?,?,?,?,?)",
                [(BOT_ID, GID, "Assistant", "bot", "assistant"),
                 (HUMAN_ID, GID, "Human", "human", None)],
            )
            c.commit()
        from executors import registry
        registry.discover()
        # Defensive isolation: a prior test that hydrated a group (e.g.
        # test_cell_17_lifecycle hydrates group 1) leaves the process-global workflow
        # orchestrator singleton keyed on that group_id. For GID=1 that makes
        # dispatch take the (stale) workflow path instead of free-form @mention
        # routing, so the bot never replies. Reset it to a clean slate.
        import core.workflow as _wf
        _wf._orch._state.clear()
        _wf._group_orch.clear()
        from runtime.lifecycle import manager as _lm
        _lm._active_groups.clear()

    async def asyncTearDown(self):
        # This is a heavyweight integration test that runs a real fire-and-forget
        # bot pipeline; drain/clear the process-global task registries so leaked
        # tasks from this loop don't bleed into later tests.
        from core import bg
        from executors import tool_executor
        bg.abort_group(GID)
        bg._bg_tasks.clear()
        bg._group_tasks.clear()
        # Steering is per-ctx (ctx.steer_channel) now — no global queue registry to clear.
        # setUp's registry.discover() registers the workspace tools into the global
        # tool_executor; reset it so a later test that assumes an empty executor
        # (e.g. test_decoupled_executor, which then takes tool_loop's no-tools
        # streaming branch) isn't perturbed by this heavyweight integration test.
        tool_executor._registry.clear()
        tool_executor.clear_before_hooks()
        tool_executor.clear_after_hooks()
        await db.aclose_writer()
        db.DB_PATH, _writer.DB_PATH = self._orig_db, self._orig_w
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def _bot_replies(self):
        async with db.connect(self.group) as c:
            cur = await c.execute(
                "SELECT content FROM messages WHERE member_id=?", (BOT_ID,))
            return [r[0] for r in await cur.fetchall()]

    async def test_dispatch_runs_bot_and_persists_to_group_db(self):
        async def mock_stream(*a, **k):
            yield "cell reply"
            if isinstance(k.get("usage_out"), list):
                k["usage_out"].append({"input_tokens": 1, "output_tokens": 1})

        call_once = AsyncMock(return_value={
            "type": "text", "content": "cell reply", "usage": {}})

        m = "executors.plugins.tool_loop_v1."
        with patch("core.orchestration.ai_service.call_ai_once", new=call_once), \
             patch("core.orchestration.ai_service.call_ai_stream_messages", side_effect=mock_stream), \
             patch(m + "list_skills_all", new=AsyncMock(return_value=[])), \
             patch(m + "load_always_skills", new=AsyncMock(return_value=[])), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch(m + "format_context_blocks", return_value=""), \
             patch(m + "filter_skills_by_context", side_effect=lambda s, _: s), \
             patch(m + "append_log", new=AsyncMock()), \
             patch(m + "archive_run", new=AsyncMock()), \
             patch("executors.compact.maybe_compact_db_history", new=AsyncMock()):
            with db.bind_db(self.group):
                channel_message = {
                    "group_id": GID, "member_id": HUMAN_ID,
                    "content": "@Assistant hello", "trace_id": "tr",
                    "channel_message_key": "channel.v1|dedup-cell-message",
                }
                await dispatch_user_message(channel_message)
                await dispatch_user_message(channel_message)
            # bot run is fire-and-forget (bg.spawn_group); wait for it to land
            for _ in range(250):
                if await self._bot_replies():
                    break
                await asyncio.sleep(0.02)

        replies = await self._bot_replies()
        self.assertTrue(replies, "bot reply was not persisted to the group DB")
        self.assertIn("cell reply", replies[-1])
        # user message landed in the GROUP db, members stayed CENTRAL-only
        async with db.connect(self.group) as c:
            cur = await c.execute("SELECT COUNT(*) FROM messages WHERE member_id=?", (HUMAN_ID,))
            self.assertEqual((await cur.fetchone())[0], 1)
            cur = await c.execute("SELECT name FROM sqlite_master WHERE name='members'")
            self.assertIsNone(await cur.fetchone())

    async def test_wake_trigger_routes_message_to_configured_bot(self):
        """Cron/alert wake frames must execute the selected bot in the Worker."""
        async def mock_stream(*a, **k):
            yield "wake reply"
            if isinstance(k.get("usage_out"), list):
                k["usage_out"].append({"input_tokens": 1, "output_tokens": 1})

        m = "executors.plugins.tool_loop_v1."
        with patch("core.orchestration.ai_service.call_ai_once", new=AsyncMock(return_value={
            "type": "text", "content": "wake reply", "usage": {}})), \
             patch("core.orchestration.ai_service.call_ai_stream_messages", side_effect=mock_stream), \
             patch(m + "list_skills_all", new=AsyncMock(return_value=[])), \
             patch(m + "load_always_skills", new=AsyncMock(return_value=[])), \
             patch(m + "load_context_files", new=AsyncMock(return_value=[])), \
             patch(m + "format_context_blocks", return_value=""), \
             patch(m + "filter_skills_by_context", side_effect=lambda s, _: s), \
             patch(m + "append_log", new=AsyncMock()), \
             patch(m + "archive_run", new=AsyncMock()), \
             patch("executors.compact.maybe_compact_db_history", new=AsyncMock()):
            with db.bind_db(self.group):
                await dispatch_wake_trigger({
                    "type": "wake_trigger",
                    "group_id": GID,
                    "bot_id": BOT_ID,
                    "content": "scheduled standup",
                })
                for _ in range(250):
                    if await self._bot_replies():
                        break
                    await asyncio.sleep(0.02)

        replies = await self._bot_replies()
        self.assertTrue(replies, "wake trigger did not execute the target bot")
        self.assertIn("wake reply", replies[-1])
        async with db.connect(self.group) as c:
            cur = await c.execute(
                "SELECT content, member_id, sender_type, meta FROM messages "
                "WHERE member_id=0 ORDER BY id DESC LIMIT 1"
            )
            wake = await cur.fetchone()
        self.assertIsNotNone(wake, "wake message was not persisted to the group")
        self.assertEqual(wake[0], "scheduled standup")
        self.assertEqual(wake[2], "system")
        self.assertIn("wake_trigger", wake[3])


class TestEntryFactory(unittest.TestCase):
    def test_build_worker_wires_real_dispatch(self):
        from runtime.entry import build_worker
        from runtime.dispatch import dispatch_user_message
        w = build_worker("w3", "/tmp/nuke_x.sock")
        self.assertIs(w._dispatch, dispatch_user_message)
        self.assertEqual(w.worker_id, "w3")

    def test_build_supervisor(self):
        from runtime.entry import build_supervisor
        sup = build_supervisor("/tmp/nuke_y.sock")
        self.assertEqual(sup.addr, "/tmp/nuke_y.sock")


class TestEntrySupervisorLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_run_supervisor_stops_on_cancellation(self):
        import runtime.entry as entry

        class _FakeSupervisor:
            def __init__(self):
                self.started = False
                self.stopped = False

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

            async def send_to_worker(self, group_id, frame):
                return None

        fake = _FakeSupervisor()

        with patch.object(entry, "build_supervisor", return_value=fake):
            task = asyncio.create_task(entry.run_supervisor("/tmp/sup.sock", num_workers=2))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(fake.started)
        self.assertTrue(fake.stopped)

    async def test_run_supervisor_stops_scheduler_before_supervisor(self):
        import runtime.entry as entry

        events = []

        class _FakeSupervisor:
            async def start(self):
                events.append("sup.start")

            async def stop(self):
                events.append("sup.stop")

            async def send_to_worker(self, group_id, frame):
                return None

        fake = _FakeSupervisor()

        async def fake_scheduler_start():
            events.append("scheduler.start")

        def fake_scheduler_stop():
            events.append("scheduler.stop")

        with patch.object(entry, "build_supervisor", return_value=fake), \
             patch.object(entry.scheduler, "start", new=fake_scheduler_start), \
             patch.object(entry.scheduler, "stop", new=fake_scheduler_stop):
            task = asyncio.create_task(entry.run_supervisor("/tmp/sup.sock", num_workers=2))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(events, ["sup.start", "scheduler.start", "scheduler.stop", "sup.stop"])


if __name__ == "__main__":
    unittest.main()
