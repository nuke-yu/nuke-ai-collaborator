"""
tests/test_sessions.py — Agent session and session events migration tests

Covers:
  1. migration_004 — agent_sessions and session_events tables creation
  2. Schema structure validation
  3. Idempotency
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
import db.migrations as _migrations_mod

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_sessions.db")


def _use_test_db():
    _db_mod.DB_PATH = _TEST_DB


def _restore_db(orig):
    _db_mod.DB_PATH = orig


class TestMigration004(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import db.migrations as m
        self._orig_migrations = list(m.MIGRATIONS)

    async def asyncTearDown(self):
        import db.migrations as m
        m.MIGRATIONS = self._orig_migrations
        _restore_db(self._orig)
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_migration_004_creates_agent_sessions(self):
        import aiosqlite
        from db.migrations import migration_004, run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_sessions'"
            ) as cur:
                row = await cur.fetchone()
        self.assertIsNotNone(row)

    async def test_migration_004_creates_session_events(self):
        import aiosqlite
        from db.migrations import migration_004, run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_events'"
            ) as cur:
                row = await cur.fetchone()
        self.assertIsNotNone(row)

    async def test_migration_004_idempotent(self):
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        # Running again must not raise
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)


class TestSessionStore(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)

    async def asyncTearDown(self):
        _restore_db(self._orig)
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_create_session(self):
        from sessions.store import create_session, get_session
        sid = await create_session(
            session_id="s1",
            bot_id=1, group_id=1,
            config={"system_prompt": "hi", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="hello",
        )
        self.assertEqual(sid, "s1")
        row = await get_session("s1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["bot_id"], 1)

    async def test_append_and_get_events(self):
        from sessions.store import create_session, append_event, get_events
        await create_session(
            session_id="s2", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="test",
        )
        await append_event("s2", "session_start", {"user_content": "test"})
        await append_event("s2", "tool_call", {"tool_call_id": "t1", "tool_name": "read_file", "arguments": {}})
        events = await get_events("s2")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "session_start")
        self.assertEqual(events[1]["event_type"], "tool_call")

    async def test_update_session_status(self):
        from sessions.store import create_session, update_session_status, get_session
        await create_session(
            session_id="s3", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="x",
        )
        await update_session_status("s3", "completed")
        row = await get_session("s3")
        self.assertEqual(row["status"], "completed")

    async def test_get_orphaned_sessions(self):
        from sessions.store import create_session, get_orphaned_sessions
        await create_session(
            session_id="s4", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="y",
        )
        orphans = await get_orphaned_sessions()
        ids = [o["id"] for o in orphans]
        self.assertIn("s4", ids)

    async def test_add_tokens(self):
        from sessions.store import create_session, add_tokens, get_session
        await create_session(
            session_id="s5", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="z",
        )
        await add_tokens("s5", input_tokens=100, output_tokens=50)
        await add_tokens("s5", input_tokens=20, output_tokens=10)
        row = await get_session("s5")
        self.assertEqual(row["input_tokens"], 120)
        self.assertEqual(row["output_tokens"], 60)
        # cache columns default to 0 when not provided
        self.assertEqual(row["cache_read_tokens"], 0)
        self.assertEqual(row["cache_creation_tokens"], 0)

    async def test_get_session_includes_cost_usd(self):
        from sessions.store import create_session, add_tokens, get_session
        from ai.pricing import calculate_cost
        await create_session(
            session_id="s7", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "claude",
                    "model_name": "claude-sonnet-4-5", "temperature": 0.7, "max_tokens": 4096},
            user_message="z",
        )
        await add_tokens("s7", input_tokens=1000, output_tokens=500,
                         cache_read_tokens=200, cache_creation_tokens=100)
        row = await get_session("s7")
        expected = calculate_cost("claude", "claude-sonnet-4-5", {
            "input_tokens": 1000, "output_tokens": 500,
            "cache_read_tokens": 200, "cache_creation_tokens": 100,
        })
        self.assertAlmostEqual(row["cost_usd"], expected, places=12)
        self.assertGreater(row["cost_usd"], 0)

    async def test_add_tokens_with_cache(self):
        from sessions.store import create_session, add_tokens, get_session
        await create_session(
            session_id="s6", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="z",
        )
        await add_tokens("s6", input_tokens=100, output_tokens=50,
                         cache_read_tokens=30, cache_creation_tokens=10)
        await add_tokens("s6", input_tokens=20, output_tokens=10,
                         cache_read_tokens=5, cache_creation_tokens=2)
        row = await get_session("s6")
        self.assertEqual(row["input_tokens"], 120)
        self.assertEqual(row["output_tokens"], 60)
        self.assertEqual(row["cache_read_tokens"], 35)
        self.assertEqual(row["cache_creation_tokens"], 12)


class TestMessageReconstruction(unittest.IsolatedAsyncioTestCase):

    def _make_event(self, etype, payload):
        return {"event_type": etype, "payload": payload}

    def test_reconstruct_basic_conversation(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "You are a helper.", "provider": "deepseek"}
        events = [
            self._make_event("session_start", {"user_content": "What is 2+2?"}),
            self._make_event("llm_response", {
                "content": "It is 4.", "tool_calls": None,
                "input_tokens": 10, "output_tokens": 5,
            }),
        ]
        msgs = reconstruct_messages(config, events)
        self.assertEqual(msgs[0], {"role": "system", "content": "You are a helper."})
        self.assertEqual(msgs[1], {"role": "user", "content": "What is 2+2?"})
        self.assertEqual(msgs[2], {"role": "assistant", "content": "It is 4."})
        self.assertEqual(len(msgs), 3)

    def test_reconstruct_with_tool_calls(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "sys", "provider": "deepseek"}
        tool_call_block = [{"id": "tc1", "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"}}]
        events = [
            self._make_event("session_start", {"user_content": "Read the file"}),
            self._make_event("llm_response", {
                "content": "", "tool_calls": tool_call_block,
                "input_tokens": 5, "output_tokens": 2,
            }),
            self._make_event("tool_call", {
                "tool_call_id": "tc1", "tool_name": "read_file", "arguments": {"path": "a.txt"},
            }),
            self._make_event("tool_result", {
                "tool_call_id": "tc1", "result": "file content", "is_error": False,
            }),
            self._make_event("llm_response", {
                "content": "The file says: file content", "tool_calls": None,
                "input_tokens": 10, "output_tokens": 8,
            }),
        ]
        msgs = reconstruct_messages(config, events)
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "assistant"])
        self.assertEqual(msgs[3]["content"], "file content")
        self.assertEqual(msgs[3]["tool_call_id"], "tc1")

    def test_reconstruct_no_system_prompt(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "", "provider": "deepseek"}
        events = [
            self._make_event("session_start", {"user_content": "hello"}),
        ]
        msgs = reconstruct_messages(config, events)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")

    def test_reconstruct_skips_child_fork_events(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "s", "provider": "deepseek"}
        events = [
            self._make_event("session_start", {"user_content": "do it"}),
            self._make_event("child_fork", {"child_session_id": "c1", "skill_name": "sk"}),
            self._make_event("child_join", {"child_session_id": "c1", "result": "done"}),
            self._make_event("llm_response", {
                "content": "finished", "tool_calls": None,
                "input_tokens": 5, "output_tokens": 3,
            }),
        ]
        msgs = reconstruct_messages(config, events)
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["system", "user", "assistant"])

    def test_reconstruct_warns_on_unknown_event_type(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "s", "provider": "deepseek"}
        events = [
            self._make_event("session_start", {"user_content": "do it"}),
            self._make_event("user_interrupt", {"at_iter": 2}),  # unknown type
        ]
        with self.assertLogs("sessions.recovery", level="WARNING") as cm:
            msgs = reconstruct_messages(config, events)
        self.assertTrue(any("user_interrupt" in line for line in cm.output))
        # unknown event must not add a message
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])

    def test_reconstruct_no_warning_for_ignored_types(self):
        from sessions.recovery import reconstruct_messages
        import logging
        config = {"system_prompt": "s", "provider": "deepseek"}
        events = [
            self._make_event("session_start", {"user_content": "do it"}),
            self._make_event("tool_call", {"tool_name": "read_file"}),
            self._make_event("child_fork", {"child_session_id": "c1"}),
        ]
        logger = logging.getLogger("sessions.recovery")
        with self.assertNoLogs(logger, level="WARNING"):
            reconstruct_messages(config, events)


class TestRecoverAll(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)

    async def asyncTearDown(self):
        _restore_db(self._orig)
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def _create_orphan(self, sid, user_msg="hello", parent_id=None):
        from sessions.store import create_session, append_event
        await create_session(
            session_id=sid, bot_id=99, group_id=1,
            config={"system_prompt": "s", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message=user_msg,
            parent_id=parent_id,
        )
        await append_event(sid, "session_start", {"user_content": user_msg})

    async def test_no_orphans_calls_nothing(self):
        from sessions.recovery import recover_all
        called = []
        await recover_all(dispatcher=called.append)
        self.assertEqual(called, [])

    async def test_completed_session_not_recovered(self):
        from sessions.store import create_session, update_session_status
        from sessions.recovery import recover_all
        await create_session(
            session_id="done1", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="x",
        )
        await update_session_status("done1", "completed")
        called = []
        await recover_all(dispatcher=called.append)
        self.assertEqual(called, [])

    async def test_orphan_with_only_start_event_dispatched(self):
        from sessions.recovery import recover_all
        from sessions.store import get_session
        await self._create_orphan("orph1", "hello world")
        dispatched = []
        await recover_all(dispatcher=dispatched.append)
        self.assertEqual(len(dispatched), 1)
        payload = dispatched[0]
        self.assertEqual(payload["session_id"], "orph1")
        self.assertEqual(payload["bot_id"], 99)
        self.assertEqual(payload["group_id"], 1)
        self.assertIn("messages", payload)
        # session_start gave us system + user messages
        self.assertEqual(len(payload["messages"]), 2)
        # session should now be marked as 'recovering'
        row = await get_session("orph1")
        self.assertEqual(row["status"], "recovering")

    async def test_dangling_idempotent_tool_marked_for_retry(self):
        from sessions.store import create_session, append_event
        from sessions.recovery import recover_all
        await create_session(
            session_id="idem1", bot_id=1, group_id=1,
            config={"system_prompt": "s", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="search",
        )
        await append_event("idem1", "session_start", {"user_content": "search"})
        await append_event("idem1", "tool_call", {
            "tool_call_id": "t1", "tool_name": "web_search", "arguments": {"query": "x"},
        })
        # no tool_result — process crashed before tool returned
        dispatched = []
        await recover_all(dispatcher=dispatched.append)
        # idempotent tool → still dispatch (roll back to before tool_call)
        self.assertEqual(len(dispatched), 1)
        # dangling tool_call should NOT appear in reconstructed messages
        msgs = dispatched[0]["messages"]
        roles = [m["role"] for m in msgs]
        self.assertNotIn("tool", roles)

    async def test_dangling_side_effect_tool_marks_needs_review(self):
        from sessions.store import create_session, append_event, get_session
        from sessions.recovery import recover_all
        await create_session(
            session_id="side1", bot_id=1, group_id=1,
            config={"system_prompt": "s", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="run shell",
        )
        await append_event("side1", "session_start", {"user_content": "run shell"})
        await append_event("side1", "tool_call", {
            "tool_call_id": "t2", "tool_name": "run_shell", "arguments": {"cmd": "rm -rf /"},
        })
        dispatched = []
        await recover_all(dispatcher=dispatched.append)
        # side-effectful → should NOT be dispatched
        self.assertEqual(len(dispatched), 0)
        row = await get_session("side1")
        self.assertEqual(row["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
