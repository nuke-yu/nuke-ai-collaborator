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
from ai.tool_events import _extract_command, _extract_files, _summarize, record_event

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


if __name__ == "__main__":
    unittest.main()
