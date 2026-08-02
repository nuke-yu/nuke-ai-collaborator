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
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
import db.writer as _writer_mod
from db.schema import init_db
import db.migrations as _migrations_mod

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_sessions.db")


def _use_test_db():
    # sessions.store writes through the serialized writer (db.write_connect),
    # which resolves its default path from db.writer.DB_PATH — a *different*
    # module global than db.DB_PATH. Patch both or writes land in the real DB.
    _db_mod.DB_PATH = _TEST_DB
    _writer_mod.DB_PATH = _TEST_DB


def _restore_db(orig):
    # Both modules default to the same db/chat.db, so `orig` restores both.
    _db_mod.DB_PATH = orig
    _writer_mod.DB_PATH = orig


async def _seed_parents(group_id: int = 1, bot_ids=(1, 99)):
    # agent_sessions.bot_id/group_id are real FKs (DFT-028). Seed the parent
    # rows so session inserts don't violate foreign_keys=ON.
    import aiosqlite
    async with aiosqlite.connect(_TEST_DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups (id, name) VALUES (?, ?)",
            (group_id, "g"),
        )
        for bid in bot_ids:
            await db.execute(
                "INSERT OR IGNORE INTO members (id, group_id, name, type) "
                "VALUES (?, ?, ?, 'bot')",
                (bid, group_id, "bot"),
            )
        await db.commit()


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
        await _seed_parents()

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
        start_observability = events[0]["payload"]["_observability"]
        self.assertEqual(start_observability["classes"], ["timeline"])
        self.assertTrue(start_observability["business_significant"])
        tool_observability = events[1]["payload"]["_observability"]
        self.assertEqual(tool_observability["effects"], ["read"])
        self.assertFalse(tool_observability["business_significant"])

    async def test_large_payload_is_projected_and_hydrated_for_recovery(self):
        from sessions.store import create_session, append_event, get_events
        await create_session(
            session_id="artifact-session", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek", "model_name": "deepseek-chat"},
            user_message="test",
        )
        secret = "sk-" + "a" * 30
        await append_event("artifact-session", "llm_response", {
            "content": secret + (" result" * 1_000),
            "model": "test-model",
        })

        projected = (await get_events("artifact-session"))[0]["payload"]
        self.assertIn("_artifact", projected)
        self.assertIn("_summary", projected)
        self.assertNotIn("content", projected)

        hydrated = (await get_events("artifact-session", hydrate_artifacts=True))[0]["payload"]
        self.assertIn("content", hydrated)
        self.assertNotIn(secret, hydrated["content"])
        self.assertIn("[REDACTED]", hydrated["content"])

        import aiosqlite
        async with aiosqlite.connect(_TEST_DB) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM observation_artifacts WHERE group_id = 1"
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], 1)

    async def test_missing_recovery_artifact_fails_closed(self):
        from observability import PayloadArtifactError
        from sessions.store import create_session, append_event, get_events
        await create_session(
            session_id="missing-artifact", bot_id=1, group_id=1,
            config={"system_prompt": ""}, user_message="test",
        )
        await append_event("missing-artifact", "llm_response", {"content": "x" * 10_000})
        import aiosqlite
        async with aiosqlite.connect(_TEST_DB) as conn:
            await conn.execute("DELETE FROM observation_artifacts WHERE group_id = 1")
            await conn.commit()
        with self.assertRaises(PayloadArtifactError):
            await get_events("missing-artifact", hydrate_artifacts=True)

    async def test_update_session_status(self):
        from sessions.store import create_session, update_session_status, get_events, get_session
        await create_session(
            session_id="s3", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="x",
        )
        await update_session_status("s3", "completed")
        row = await get_session("s3")
        self.assertEqual(row["status"], "completed")
        events = await get_events("s3")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "session_status")
        self.assertEqual(events[0]["payload"]["from_status"], "running")
        self.assertEqual(events[0]["payload"]["status"], "completed")
        self.assertEqual(
            events[0]["payload"]["_observability"]["effects"], ["lifecycle"]
        )

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
            self._make_event("permission_requested", {"permission_id": "perm_1"}),
            self._make_event("permission_approved", {"permission_id": "perm_1"}),
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
        await _seed_parents()

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

    def test_list_workspace_is_safe_to_replay(self):
        from sessions.recovery import is_idempotent

        self.assertTrue(is_idempotent("list_workspace"))

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

    async def test_orphan_with_only_start_event_abandoned(self):
        # Chat semantics: orphaned ('running') sessions are dropped (marked 'failed'),
        # not resumed — and no recovery prompt is sent.
        from sessions.recovery import recover_all
        from sessions.store import get_session
        await self._create_orphan("orph1", "hello world")

        from unittest.mock import AsyncMock, patch
        with patch("ws_manager.manager.broadcast", new=AsyncMock()) as mock_broadcast:
            await recover_all()
            row = await get_session("orph1")
            self.assertEqual(row["status"], "failed")
            mock_broadcast.assert_not_called()

    async def test_dangling_idempotent_tool_abandoned(self):
        from sessions.store import create_session, append_event, get_session
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

        await recover_all()  # abandoned regardless of any dangling tool
        row = await get_session("idem1")
        self.assertEqual(row["status"], "failed")

    async def test_dangling_side_effect_tool_abandoned(self):
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
        # Abandoned: never dispatched, just marked failed.
        self.assertEqual(len(dispatched), 0)
        row = await get_session("side1")
        self.assertEqual(row["status"], "failed")

    async def test_workflow_lookup_failure_logs_and_fails_closed(self):
        from sessions.store import create_session, get_session
        from sessions.recovery import recover_all

        await create_session(
            session_id="wferr1", bot_id=1, group_id=1,
            config={"system_prompt": "s", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="hello",
        )

        with patch("core.workflow.is_workflow_participant", side_effect=RuntimeError("lookup failed")), \
             self.assertLogs("sessions.recovery", level="ERROR") as logs:
            await recover_all()

        row = await get_session("wferr1")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(any("failed to resolve workflow participation" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
