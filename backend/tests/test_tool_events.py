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

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_tool_events.db")


async def _fetch_events(group_id: int) -> list[dict]:
    async with database.connect(TEST_DB_PATH) as db:
        async with db.execute(
            "SELECT ts, group_id, bot_id, thread_id, tool, args_summary, "
            "result_summary, is_error, files_touched, command "
            "FROM tool_events WHERE group_id=? ORDER BY id",
            (group_id,),
        ) as cur:
            rows = await cur.fetchall()
    cols = ["ts", "group_id", "bot_id", "thread_id", "tool", "args_summary",
            "result_summary", "is_error", "files_touched", "command"]
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


if __name__ == "__main__":
    unittest.main()
