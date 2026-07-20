"""L1 tool_events tests — deterministic event log (zero LLM, fail-open).

Mirrors tests/test_memory.py DB setup: point db.DB_PATH at a temp file and run
init_db() (which runs migrations, creating tool_events via migration_025).
"""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import db as database
from ai import tool_events
from ai.tool_events import (
    _extract_command,
    _extract_files,
    _summarize,
    fetch_events,
    record_event,
    search_events,
    timeline_events,
)
from ai.execution_runs import finish_run, start_run
from ai.cases import assemble_case, task_signature
from ai.experiences import complete_usage, distill_case, recall_experiences

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_tool_events.db")


async def _fetch_events(group_id: int) -> list[dict]:
    async with database.connect(TEST_DB_PATH) as db:
        async with db.execute(
            "SELECT ts, group_id, bot_id, thread_id, tool, args_summary, "
            "result_summary, is_error, files_touched, command, run_id, step_id, attempt_id "
            "FROM tool_events WHERE group_id=? ORDER BY id",
            (group_id,),
        ) as cur:
            rows = await cur.fetchall()
    cols = ["ts", "group_id", "bot_id", "thread_id", "tool", "args_summary",
            "result_summary", "is_error", "files_touched", "command", "run_id",
            "step_id", "attempt_id"]
    return [dict(zip(cols, r)) for r in rows]


class PureHelpersTest(unittest.TestCase):
    """The extraction/summarize helpers are pure — no DB, no model."""

    def test_extract_files_from_path(self):
        self.assertEqual(_extract_files({"path": "a/b.py"}), ["a/b.py"])
        self.assertEqual(_extract_files({"file_path": "x.txt"}), ["x.txt"])

    def test_extract_files_from_edits_array_dedup(self):
        args = {"path": "a.py", "edits": [{"path": "a.py"}, {"path": "b.py"}]}
        self.assertEqual(_extract_files(args), ["a.py", "b.py"])

    def test_extract_files_empty_when_none(self):
        self.assertEqual(_extract_files({"cmd": "ls"}), [])
        self.assertEqual(_extract_files("not a dict"), [])

    def test_extract_command_only_for_shell(self):
        self.assertEqual(_extract_command("run_shell", {"cmd": "ls -la"}), "ls -la")
        self.assertIsNone(_extract_command("read_file", {"cmd": "ls"}))
        self.assertIsNone(_extract_command("run_shell", {}))

    def test_summarize_truncates_oversize(self):
        out = _summarize("x" * 10_000, cap=2_000)
        self.assertLessEqual(len(out), 2_100)  # head+tail+marker
        self.assertIn("elided", out)

    def test_summarize_redacts_secrets(self):
        # An AWS access key id is one of redaction's high-confidence patterns.
        out = _summarize("token AKIAIOSFODNN7EXAMPLE here")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", out)

    def test_summarize_json_serializes_dict(self):
        out = _summarize({"path": "a.py", "n": 1})
        self.assertEqual(json.loads(out), {"path": "a.py", "n": 1})


class RecordEventTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self._orig
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    async def test_record_event_inserts_row(self):
        await record_event(
            group_id=7, bot_id=3, tool="edit_file",
            arguments={"path": "src/x.py", "old_string": "a", "new_string": "b"},
            result="ok", is_error=False, thread_id="t1",
        )
        rows = await _fetch_events(7)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["tool"], "edit_file")
        self.assertEqual(row["bot_id"], 3)
        self.assertEqual(row["thread_id"], "t1")
        self.assertEqual(row["is_error"], 0)
        self.assertEqual(json.loads(row["files_touched"]), ["src/x.py"])
        self.assertIsNone(row["command"])
        self.assertIn("x.py", row["args_summary"])

    async def test_record_event_shell_captures_command(self):
        await record_event(
            group_id=7, bot_id=1, tool="run_shell",
            arguments={"cmd": "pytest -q"}, result="3 passed", is_error=False,
        )
        rows = await _fetch_events(7)
        self.assertEqual(rows[0]["command"], "pytest -q")
        self.assertEqual(json.loads(rows[0]["files_touched"]), [])

    async def test_record_event_marks_error(self):
        await record_event(
            group_id=7, bot_id=1, tool="read_file",
            arguments={"path": "missing"}, result="[错误] 文件不存在", is_error=True,
        )
        self.assertEqual((await _fetch_events(7))[0]["is_error"], 1)

    async def test_record_event_persists_execution_identity(self):
        await record_event(
            group_id=7, bot_id=1, tool="read_file", arguments={"path": "a.py"},
            result="ok", is_error=False, run_id="run-1",
            step_id="run-1:step:2", attempt_id="call-3",
        )
        row = (await _fetch_events(7))[0]
        self.assertEqual(row["run_id"], "run-1")
        self.assertEqual(row["step_id"], "run-1:step:2")
        self.assertEqual(row["attempt_id"], "call-3")

    async def test_group_isolation(self):
        await record_event(group_id=10, bot_id=1, tool="read_file", arguments={}, result="a", is_error=False)
        await record_event(group_id=20, bot_id=1, tool="read_file", arguments={}, result="b", is_error=False)
        self.assertEqual(len(await _fetch_events(10)), 1)
        self.assertEqual(len(await _fetch_events(20)), 1)

    async def test_none_group_is_noop(self):
        # Must not raise and must not write anything.
        await record_event(group_id=None, bot_id=1, tool="read_file", arguments={}, result="x", is_error=False)
        # no group to query; absence of exception is the assertion

    async def test_dispatch_tool_records_event_fire_and_forget(self):
        from executors import tool_dispatch
        ctx = {"group_id": 42, "bot_id": 5}
        with patch.object(tool_dispatch.tool_executor, "has_tool", return_value=True), \
             patch.object(tool_dispatch.tool_executor, "execute",
                          new=AsyncMock(return_value=("done", False))):
            res, is_err = await tool_dispatch.dispatch_tool("read_file", {"path": "z.py"}, ctx)
        self.assertEqual((res, is_err), ("done", False))
        # drain the fire-and-forget recording task(s)
        if tool_dispatch._recording_tasks:
            await asyncio.gather(*list(tool_dispatch._recording_tasks))
        rows = await _fetch_events(42)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "read_file")
        self.assertEqual(json.loads(rows[0]["files_touched"]), ["z.py"])

    async def test_dispatch_tool_propagates_execution_identity(self):
        from executors import tool_dispatch
        ctx = {
            "group_id": 42, "bot_id": 5, "run_id": "r1",
            "step_id": "r1:step:1", "attempt_id": "c1",
        }
        with patch.object(tool_dispatch.tool_executor, "has_tool", return_value=True), \
             patch.object(tool_dispatch.tool_executor, "execute",
                          new=AsyncMock(return_value=("done", False))):
            await tool_dispatch.dispatch_tool("read_file", {"path": "z.py"}, ctx)
        if tool_dispatch._recording_tasks:
            await asyncio.gather(*list(tool_dispatch._recording_tasks))
        row = (await _fetch_events(42))[0]
        self.assertEqual((row["run_id"], row["step_id"], row["attempt_id"]),
                         ("r1", "r1:step:1", "c1"))


    async def test_dispatch_tool_logs_if_event_recording_cannot_be_scheduled(self):
        from executors import tool_dispatch
        ctx = {"group_id": 42, "bot_id": 5}

        async def fake_execute(*_args, **_kwargs):
            return "done", False

        def _boom(coro):
            coro.close()
            raise RuntimeError("task creation failed")

        with patch.object(tool_dispatch.tool_executor, "has_tool", return_value=True), \
             patch.object(tool_dispatch.tool_executor, "execute", new=fake_execute), \
             patch("executors.tool_dispatch.asyncio.create_task", side_effect=_boom), \
             self.assertLogs("executors.tool_dispatch", level="ERROR") as logs:
            res, is_err = await tool_dispatch.dispatch_tool("read_file", {"path": "z.py"}, ctx)

        self.assertEqual((res, is_err), ("done", False))
        self.assertTrue(any("failed to schedule tool event recording" in line for line in logs.output))

    async def test_dispatch_tool_logs_if_thread_id_resolution_fails(self):
        from executors import tool_dispatch
        ctx = {"group_id": 42, "bot_id": 5}

        async def fake_execute(*_args, **_kwargs):
            return "done", False

        with patch.object(tool_dispatch.tool_executor, "has_tool", return_value=True), \
             patch.object(tool_dispatch.tool_executor, "execute", new=fake_execute), \
             patch("core.workflow.current_thread_id", side_effect=RuntimeError("thread lookup failed")), \
             self.assertLogs("executors.tool_dispatch", level="WARNING") as logs:
            res, is_err = await tool_dispatch.dispatch_tool("read_file", {"path": "z.py"}, ctx)

        self.assertEqual((res, is_err), ("done", False))
        self.assertTrue(any("failed to resolve current thread id" in line for line in logs.output))


class ExecutionRunTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self._orig
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    async def test_run_lifecycle_is_durable_and_resume_safe(self):
        kwargs = dict(
            run_id="session-1", group_id=7, bot_id=3, session_id="session-1",
            thread_id="thread-1", provider="openai", model="test", executor="tool_loop_v1",
        )
        await start_run(**kwargs)
        await start_run(**kwargs)
        await finish_run(
            run_id="session-1", group_id=7, status="completed", iterations=4,
            input_tokens=120, output_tokens=30,
        )
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT status, iterations, input_tokens, output_tokens, completed_at "
                "FROM agent_runs WHERE run_id='session-1'"
            ) as cur:
                rows = await cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:4], ("completed", 4, 120, 30))
        self.assertIsNotNone(rows[0][4])

    async def test_invalid_terminal_status_rejected(self):
        with self.assertRaises(ValueError):
            await finish_run(run_id="r", group_id=7, status="running")

    async def test_assemble_case_is_deterministic_and_idempotent(self):
        self.assertEqual(task_signature(" Fix   BUG "), task_signature("fix bug"))
        records = [
            {"name": "read_file", "args": {"path": "a.py"}, "result": "ok", "is_error": False},
            {"name": "run_shell", "args": {"cmd": "pytest"}, "result": "failed", "is_error": True},
        ]
        case_id = await assemble_case(run_id="r1", group_id=7, bot_id=3,
                                      task="Fix bug", outcome="completed", tool_records=records)
        await assemble_case(run_id="r1", group_id=7, bot_id=3,
                            task="Fix bug", outcome="completed", tool_records=records)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT case_id, attempts, outcome, files_touched FROM agent_cases") as cur:
                rows = await cur.fetchall()
        self.assertEqual(rows, [(case_id, 2, "completed", '["a.py"]')])

    async def test_distill_case_requires_failure_then_completion(self):
        plain = await assemble_case(run_id="plain", group_id=7, bot_id=3, task="read file",
                                    outcome="completed", tool_records=[])
        self.assertIsNone(await distill_case(plain, 7))
        corrected = await assemble_case(
            run_id="fixed", group_id=7, bot_id=3, task="fix tests", outcome="completed",
            tool_records=[{"name":"run_shell","args":{"cmd":"pytest"},
                           "result":"failed then fixed","is_error":True}],
        )
        record_id = await distill_case(corrected, 7)
        self.assertEqual(record_id, await distill_case(corrected, 7))
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT kind,confidence,source_ids FROM memory_records") as cur:
                rows = await cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "experience")
        self.assertEqual(json.loads(rows[0][2]), [corrected])

    async def test_recall_tracks_injection_and_execution_cost(self):
        case_id = await assemble_case(
            run_id="source", group_id=7, bot_id=3, task="修复数据库迁移失败",
            outcome="completed", tool_records=[{"name":"run_shell","args":{},"result":"migration failed","is_error":True}],
        )
        record_id = await distill_case(case_id, 7)
        context, ids = await recall_experiences(
            query="数据库迁移怎么修复", run_id="target", group_id=7, bot_id=3)
        self.assertEqual(ids, [record_id])
        self.assertIn("prior execution experience", context)
        await complete_usage(record_ids=ids, run_id="target", group_id=7,
                             outcome="completed", input_tokens=90, output_tokens=20, tool_attempts=1)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT state,outcome,input_tokens,tool_attempts FROM experience_usage") as cur:
                row = await cur.fetchone()
        self.assertEqual(row, ("executed", "completed", 90, 1))


class RetrievalTest(unittest.IsolatedAsyncioTestCase):
    """L3 — 3-layer retrieval over tool_events."""

    async def asyncSetUp(self):
        self._orig = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
        # Seed a small chronological log in group 1; group 2 gets a decoy.
        await record_event(group_id=1, bot_id=1, tool="read_file",
                           arguments={"path": "alpha.py"}, result="content of alpha", is_error=False)
        await record_event(group_id=1, bot_id=1, tool="edit_file",
                           arguments={"path": "beta.py"}, result="edited beta", is_error=False)
        await record_event(group_id=1, bot_id=1, tool="run_shell",
                           arguments={"cmd": "pytest beta"}, result="1 failed", is_error=True)
        await record_event(group_id=2, bot_id=9, tool="edit_file",
                           arguments={"path": "alpha.py"}, result="other group", is_error=False)

    async def asyncTearDown(self):
        database.DB_PATH = self._orig
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    async def test_search_returns_index_only(self):
        rows = await search_events(1, "beta")
        # matches edit_file(beta.py) and run_shell(pytest beta)
        self.assertEqual({r["tool"] for r in rows}, {"edit_file", "run_shell"})
        self.assertNotIn("args_summary", rows[0])  # index, not full

    async def test_search_empty_query_returns_recent(self):
        rows = await search_events(1, "", limit=10)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["tool"], "run_shell")  # newest first

    async def test_search_filters_by_tool(self):
        rows = await search_events(1, "", tool="edit_file")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "edit_file")

    async def test_search_group_scoped(self):
        # "alpha" exists in both groups; group 1 must not see group 2's row.
        rows = await search_events(1, "alpha")
        self.assertTrue(all(r["tool"] == "read_file" for r in rows))
        self.assertEqual(len(rows), 1)

    async def test_timeline_around_anchor(self):
        ids = [r["id"] for r in await search_events(1, "", limit=10)][::-1]  # chronological
        anchor = ids[1]  # the edit_file row
        rows = await timeline_events(1, anchor, before=1, after=1)
        tools = [r["tool"] for r in rows]
        self.assertEqual(tools, ["read_file", "edit_file", "run_shell"])

    async def test_fetch_full_rows_group_scoped(self):
        all_rows = await search_events(1, "", limit=10)
        ids = [r["id"] for r in all_rows]
        full = await fetch_events(1, ids)
        self.assertEqual(len(full), 3)
        self.assertIn("alpha", "".join(r["result_summary"] for r in full))
        # cross-group id leakage guard: fetching a group-2 id under group 1 = empty
        g2 = await search_events(2, "")
        self.assertEqual(await fetch_events(1, [g2[0]["id"]]), [])


class HandlerTest(unittest.IsolatedAsyncioTestCase):
    """L3 builtin tool handlers — context-scoped, formatted output."""

    async def asyncSetUp(self):
        self._orig = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()
        await record_event(group_id=1, bot_id=1, tool="run_shell",
                           arguments={"cmd": "pytest -q"}, result="1 failed", is_error=True)

    async def asyncTearDown(self):
        database.DB_PATH = self._orig
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    async def test_search_handler_renders_index(self):
        from executors.plugins.memory_search_tool import _handle_search_memory
        out = await _handle_search_memory(query="pytest", context={"group_id": 1, "bot_id": 1})
        self.assertIn("run_shell", out)
        self.assertIn("pytest -q", out)
        self.assertIn("memory_fetch", out)  # next-step hint

    async def test_fetch_handler_returns_full(self):
        from executors.plugins.memory_search_tool import _handle_memory_fetch, _handle_search_memory
        # find the id via search, then fetch
        idx = await _handle_search_memory(query="", context={"group_id": 1})
        first_id = int(idx.splitlines()[2].split("|")[0].strip())
        out = await _handle_memory_fetch(ids=[first_id], context={"group_id": 1})
        self.assertIn("结果: 1 failed", out)
        self.assertIn("出错", out)  # is_error marker

    async def test_handler_requires_group_context(self):
        from executors.plugins.memory_search_tool import _handle_search_memory
        out = await _handle_search_memory(query="x", context={})
        self.assertIn("无群组上下文", out)


class CompressionTest(unittest.IsolatedAsyncioTestCase):
    """L4 — batch compression of tool_events into durable memory."""

    async def asyncSetUp(self):
        self._orig = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self._orig
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    async def _seed(self, n: int, group_id: int = 1, bot_id: int = 1):
        for i in range(n):
            await record_event(group_id=group_id, bot_id=bot_id, tool="edit_file",
                               arguments={"path": f"f{i}.py"}, result=f"edited {i}", is_error=False)

    async def _compressed_count(self, group_id: int = 1) -> int:
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM tool_events WHERE group_id=? AND compressed=1", (group_id,)
            ) as cur:
                return (await cur.fetchone())[0]

    async def test_below_threshold_is_noop(self):
        from ai import tool_events as te
        await self._seed(3)
        with patch("core.config.TOOL_EVENT_COMPRESS_THRESHOLD", 20), \
             patch("ai.client.call_ai_once", new=AsyncMock()) as mock_ai:
            await te.maybe_compress_tool_events(1, 1)
        mock_ai.assert_not_called()
        self.assertEqual(await self._compressed_count(), 0)

    async def test_compresses_and_writes_chroma(self):
        from ai import tool_events as te
        await self._seed(5)
        ai_ret = {"type": "text", "content": "- 改过 f0.py 等多个文件|0.9\n- 全部成功无报错|0.6"}
        with patch("core.config.TOOL_EVENT_COMPRESS_THRESHOLD", 5), \
             patch("ai.client.call_ai_once", new=AsyncMock(return_value=ai_ret)) as mock_ai, \
             patch("ai.memory.ChromaStore.write_fact_sync") as mock_write:
            await te.maybe_compress_tool_events(1, 1, role="dev")
        mock_ai.assert_awaited_once()
        self.assertEqual(mock_write.call_count, 2)  # two insights
        # the written memory carries tool_episode type + group scope
        _, _, meta = mock_write.call_args_list[0].args
        self.assertEqual(meta["mem_type"], "tool_episode")
        self.assertEqual(meta["group_id"], 1)
        self.assertEqual(await self._compressed_count(), 5)

    async def test_no_insight_still_advances(self):
        from ai import tool_events as te
        await self._seed(5)
        with patch("core.config.TOOL_EVENT_COMPRESS_THRESHOLD", 5), \
             patch("ai.client.call_ai_once",
                   new=AsyncMock(return_value={"type": "text", "content": "NO_INSIGHT"})), \
             patch("ai.memory.ChromaStore.write_fact_sync") as mock_write:
            await te.maybe_compress_tool_events(1, 1)
        mock_write.assert_not_called()
        self.assertEqual(await self._compressed_count(), 5)  # advanced anyway

    async def test_prune_removes_old_compressed(self):
        from ai import tool_events as te
        await self._seed(2)
        # mark them compressed + backdate ts far past retention
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute("UPDATE tool_events SET compressed=1, ts=1000")
            await db.commit()
        await te._prune_compressed(1)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM tool_events") as cur:
                self.assertEqual((await cur.fetchone())[0], 0)


class FtsSearchTest(unittest.IsolatedAsyncioTestCase):
    """L3 upgrade — FTS5 ranked search + LIKE fallback."""

    async def asyncSetUp(self):
        self._orig = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self._orig
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    async def test_fts_table_created(self):
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='tool_events_fts'"
            ) as cur:
                self.assertIsNotNone(await cur.fetchone())

    async def test_fts_matches_content_word_only(self):
        await record_event(group_id=1, bot_id=1, tool="edit_file",
                           arguments={"path": "auth/login.py"}, result="fixed token refresh", is_error=False)
        await record_event(group_id=1, bot_id=1, tool="run_shell",
                           arguments={"cmd": "grep nonsense"}, result="nothing here", is_error=False)
        rows = await search_events(1, "token")
        self.assertEqual([r["tool"] for r in rows], ["edit_file"])

    async def test_fts_keeps_index_in_sync_on_insert(self):
        await record_event(group_id=2, bot_id=1, tool="write_file",
                           arguments={"path": "x.py"}, result="created widget module", is_error=False)
        rows = await search_events(2, "widget")
        self.assertEqual(len(rows), 1)

    async def test_special_chars_do_not_crash(self):
        await record_event(group_id=3, bot_id=1, tool="run_shell",
                           arguments={"cmd": "ls"}, result="ok", is_error=False)
        for q in ['a(b', 'foo"bar', 'x OR', '* near']:
            rows = await search_events(3, q)
            self.assertIsInstance(rows, list)

    async def test_fallback_to_like_when_fts_absent(self):
        # Simulate a SQLite build without FTS5: neither the virtual table nor its
        # sync triggers exist (they're created/skipped together — never half).
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute("DROP TRIGGER IF EXISTS tool_events_fts_ai")
            await db.execute("DROP TRIGGER IF EXISTS tool_events_fts_ad")
            await db.execute("DROP TABLE tool_events_fts")
            await db.commit()
        await record_event(group_id=4, bot_id=1, tool="edit_file",
                           arguments={"path": "core/widget.py"}, result="done", is_error=False)
        rows = await search_events(4, "widget")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "edit_file")

    async def test_fts_group_scoped(self):
        await record_event(group_id=5, bot_id=1, tool="edit_file",
                           arguments={"path": "shared.py"}, result="token logic", is_error=False)
        await record_event(group_id=6, bot_id=1, tool="edit_file",
                           arguments={"path": "shared.py"}, result="token logic", is_error=False)
        rows = await search_events(5, "token")
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
