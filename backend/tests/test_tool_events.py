"""L1 tool_events tests — deterministic event log (zero LLM, fail-open).

Mirrors tests/test_memory.py DB setup: point db.DB_PATH at a temp file and run
init_db() (which runs migrations, creating tool_events via migration_025).
"""
import asyncio
import json
import os
import sqlite3
import time
import unittest
from types import SimpleNamespace
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
from ai.cases import assemble_case, build_attempt_trace, task_signature
from ai.cases import evaluate_outcome
from ai.pipeline import (
    dispatch_group,
    enqueue_missing_turn_observations,
    job_stats,
    process_case,
)
from ai.reflexion import classify_failure, maybe_inject, record_memory_injection
from ai.skill_learning import (compile_candidate, complete_skill_usage,
                               promote_skill, recall_skills,
                               resolve_skill_refs, validate_declaration)
from ai.experiences import complete_usage, decay_experiences, distill_case, recall_experiences
from ai.learning_metrics import collect_learning_shadow_metrics
from ai.usage_tracking import mark_adopted, mark_executed, mark_verified
from memory.domain import UsageKind, UsageState

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_tool_events.db")


def _corrected_trace(failure: str = "verification failed") -> list[dict]:
    return [
        {
            "name": "run_shell",
            "args": {"cmd": "pytest tests/test_memory.py -q"},
            "result": failure,
            "is_error": True,
        },
        {
            "name": "edit_file",
            "args": {"path": "backend/ai/memory.py"},
            "result": "edited",
            "is_error": False,
        },
        {
            "name": "run_shell",
            "args": {"cmd": "pytest tests/test_memory.py -q"},
            "result": "1 passed",
            "is_error": False,
        },
    ]


async def _fetch_events(group_id: int) -> list[dict]:
    async with database.connect(TEST_DB_PATH) as db:
        async with db.execute(
            "SELECT ts, group_id, bot_id, thread_id, tool, args_summary, "
            "result_summary, is_error, files_touched, command, run_id, step_id, "
            "attempt_id, memory_refs_json "
            "FROM tool_events WHERE group_id=? ORDER BY id",
            (group_id,),
        ) as cur:
            rows = await cur.fetchall()
    cols = ["ts", "group_id", "bot_id", "thread_id", "tool", "args_summary",
            "result_summary", "is_error", "files_touched", "command", "run_id",
            "step_id", "attempt_id", "memory_refs_json"]
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

    async def test_dispatch_tool_validates_strips_and_records_memory_refs(self):
        from executors import tool_dispatch
        ctx = {
            "group_id": 42,
            "bot_id": 5,
            "run_id": "r1",
            "allowed_memory_refs": ("exp:allowed",),
        }
        execute = AsyncMock(return_value=("done", False))
        with patch.object(tool_dispatch.tool_executor, "has_tool", return_value=True), \
             patch.object(tool_dispatch.tool_executor, "execute", new=execute):
            result = await tool_dispatch.dispatch_tool(
                "read_file",
                {"path": "z.py", "_memory_refs": ["exp:allowed"]},
                ctx,
            )
        self.assertEqual(result, ("done", False))
        self.assertEqual(execute.await_args.args[1], {"path": "z.py"})
        self.assertEqual(ctx["_executed_arguments"], {"path": "z.py"})
        self.assertEqual(ctx["_validated_memory_refs"], ("exp:allowed",))
        if tool_dispatch._recording_tasks:
            await asyncio.gather(*list(tool_dispatch._recording_tasks))
        row = (await _fetch_events(42))[0]
        self.assertEqual(json.loads(row["memory_refs_json"]), ["exp:allowed"])

    async def test_dispatch_tool_rejects_non_injected_memory_ref(self):
        from executors import tool_dispatch
        ctx = {
            "group_id": 42,
            "bot_id": 5,
            "allowed_memory_refs": ("exp:allowed",),
        }
        execute = AsyncMock(return_value=("should not run", False))
        with patch.object(tool_dispatch.tool_executor, "has_tool", return_value=True), \
             patch.object(tool_dispatch.tool_executor, "execute", new=execute):
            result, is_error = await tool_dispatch.dispatch_tool(
                "read_file",
                {"path": "z.py", "_memory_refs": ["exp:other-group"]},
                ctx,
            )
        self.assertTrue(is_error)
        self.assertIn("not injected", result)
        execute.assert_not_awaited()

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

    async def _verify_usage(
        self,
        kind: UsageKind,
        item_ids: list[str],
        run_id: str,
        status: UsageState,
    ) -> None:
        await mark_adopted(
            kind=kind,
            item_ids=item_ids,
            run_id=run_id,
            group_id=7,
            adopted_via="decision_trace",
            evidence={"decision_id": f"decision:{run_id}"},
        )
        await mark_executed(
            kind=kind,
            item_ids=item_ids,
            run_id=run_id,
            group_id=7,
            evidence={
                "action_match": True,
                "evidence_ids": [f"tool-event:{run_id}"],
            },
        )
        await mark_verified(
            kind=kind,
            item_ids=item_ids,
            run_id=run_id,
            group_id=7,
            status=status,
            evidence={"adapter": "test_adapter", "signal": status.value},
        )

    async def test_memory_injection_decision_persists_exact_allowlist(self):
        decision_id = await record_memory_injection(
            run_id="run:refs",
            group_id=7,
            bot_id=3,
            memory_refs=("exp:one", "skill:two@v2"),
        )
        self.assertIsNotNone(decision_id)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT decision_type,memory_refs_json FROM run_decisions
                   WHERE decision_id=?""",
                (decision_id,),
            ) as cur:
                row = await cur.fetchone()
        self.assertEqual(row[0], "memory_injection")
        self.assertEqual(
            json.loads(row[1]), ["exp:one", "skill:two@v2"]
        )

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
            async with db.execute(
                """SELECT case_id,attempts,outcome,files_touched,
                    task_family,task_concepts_json,semantic_cluster_key
                    FROM agent_cases"""
            ) as cur:
                rows = await cur.fetchall()
        self.assertEqual(rows[0][:4], (case_id, 2, "completed", '["a.py"]'))
        self.assertEqual(rows[0][4], "repair")
        self.assertEqual(json.loads(rows[0][5]), [])
        self.assertTrue(rows[0][6])

    async def test_case_attempt_trace_preserves_order_and_evidence_ids(self):
        records = _corrected_trace("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 failed")
        records[0]["step_id"] = "run:trace:step:1"
        records[0]["attempt_id"] = "call:verify-1"
        case_id = await assemble_case(
            run_id="trace",
            group_id=7,
            bot_id=3,
            task="repair trace",
            outcome="completed",
            tool_records=records,
        )
        # Reassembly replaces the snapshot rather than duplicating attempts.
        await assemble_case(
            run_id="trace",
            group_id=7,
            bot_id=3,
            task="repair trace",
            outcome="completed",
            tool_records=records,
        )

        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT ordinal,step_id,attempt_id,phase,action_tool,
                    observation_status,observation_summary,verifier_adapter,
                    verifies_task FROM agent_case_attempts
                    WHERE case_id=? ORDER BY ordinal""",
                (case_id,),
            ) as cur:
                attempts = await cur.fetchall()
        self.assertEqual(len(attempts), 3)
        self.assertEqual(
            [row[3] for row in attempts], ["verify", "recover", "verify"]
        )
        self.assertEqual(attempts[0][1:3], ("run:trace:step:1", "call:verify-1"))
        self.assertEqual(attempts[0][5], "error")
        self.assertNotIn("ghp_", attempts[0][6])
        self.assertEqual(attempts[2][7:], ("pytest", 1))

    def test_attempt_trace_uses_deterministic_fallback_ids(self):
        trace = build_attempt_trace("run:fallback", [{"name": "read_file"}])
        self.assertEqual(trace[0]["step_id"], "run:fallback:step:1")
        self.assertEqual(trace[0]["attempt_id"], "run:fallback:attempt:1")
        self.assertEqual(trace[0]["phase"], "investigate")

    async def test_distill_case_requires_failure_then_completion(self):
        plain = await assemble_case(run_id="plain", group_id=7, bot_id=3, task="read file",
                                    outcome="completed", tool_records=[])
        self.assertIsNone(await distill_case(plain, 7))
        error_only = await assemble_case(
            run_id="error-only",
            group_id=7,
            bot_id=3,
            task="claim success without retry",
            outcome="completed",
            tool_records=[
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest"},
                    "result": "1 failed",
                    "is_error": True,
                }
            ],
        )
        self.assertIsNone(await distill_case(error_only, 7))
        corrected = await assemble_case(
            run_id="fixed", group_id=7, bot_id=3, task="fix tests", outcome="completed",
            tool_records=_corrected_trace("failed then fixed"),
        )
        record_id = await distill_case(corrected, 7)
        self.assertEqual(record_id, await distill_case(corrected, 7))
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT kind,confidence,source_ids,content,metadata_json,
                    algorithm_version FROM memory_records"""
            ) as cur:
                rows = await cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "experience")
        self.assertEqual(json.loads(rows[0][2]), [corrected])
        content = json.loads(rows[0][3])
        metadata = json.loads(rows[0][4])
        self.assertEqual(rows[0][5], "experience-v2")
        self.assertEqual(content["schema_version"], "experience-v2")
        self.assertEqual(content["root_cause"]["status"], "unresolved")
        self.assertEqual(content["corrective_actions"][0]["tool"], "edit_file")
        self.assertEqual(content["verification"]["adapter"], "pytest")
        self.assertEqual(content["source_case_ids"], [corrected])
        self.assertEqual(
            metadata["environment_signature"],
            content["environment"]["signature"],
        )
        self.assertEqual(metadata["evidence_quality"], "deterministic_verified_trace")

    async def test_experience_vector_projection_matches_canonical_content(self):
        first = await assemble_case(
            run_id="canonical-1", group_id=7, bot_id=3, task="fix db",
            outcome="completed",
            tool_records=_corrected_trace("first failure"),
        )
        second = await assemble_case(
            run_id="canonical-2", group_id=7, bot_id=3, task="fix db",
            outcome="completed",
            tool_records=_corrected_trace("latest failure"),
        )

        with patch("ai.experiences._index_vector", new=AsyncMock()) as index_vector:
            record_id = await distill_case(first, 7)
            await distill_case(second, 7)

        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT content,confidence FROM memory_records WHERE record_id=?", (record_id,)
            ) as cur:
                canonical_content, confidence = await cur.fetchone()
        projected_content = index_vector.await_args_list[-1].args[1]
        projected_confidence = index_vector.await_args_list[-1].args[4]
        self.assertEqual(projected_content, canonical_content)
        self.assertEqual(projected_confidence, confidence)
        self.assertIn("latest failure", canonical_content)
        self.assertNotIn("first failure", canonical_content)

    async def test_semantic_cluster_merges_paraphrases_but_not_failure_types(self):
        first = await assemble_case(
            run_id="semantic-1",
            group_id=7,
            bot_id=3,
            task="Fix DB migration issue 123",
            outcome="completed",
            tool_records=_corrected_trace("connection timeout"),
        )
        paraphrase = await assemble_case(
            run_id="semantic-2",
            group_id=7,
            bot_id=3,
            task="repair database migration issue 456",
            outcome="completed",
            tool_records=_corrected_trace("network timed out"),
        )
        different_failure = await assemble_case(
            run_id="semantic-3",
            group_id=7,
            bot_id=3,
            task="repair database migration issue 789",
            outcome="completed",
            tool_records=_corrected_trace("permission denied"),
        )

        first_record = await distill_case(first, 7)
        self.assertEqual(first_record, await distill_case(paraphrase, 7))
        self.assertNotEqual(
            first_record, await distill_case(different_failure, 7)
        )
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT supporting_count,semantic_cluster_key,
                    environment_signature,failure_signature
                    FROM memory_records ORDER BY record_id"""
            ) as cur:
                rows = await cur.fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(row[0] for row in rows), [1, 2])
        self.assertEqual(len({row[1] for row in rows}), 1)
        self.assertTrue(all(row[2] and row[3] for row in rows))

    async def test_run_completion_is_shadow_telemetry_not_usage_evidence(self):
        case_id = await assemble_case(
            run_id="source", group_id=7, bot_id=3, task="修复数据库迁移失败",
            outcome="completed", tool_records=_corrected_trace("migration failed"),
        )
        record_id = await distill_case(case_id, 7)
        context, ids = await recall_experiences(
            query="数据库迁移怎么修复", run_id="target", group_id=7, bot_id=3)
        self.assertEqual(ids, [record_id])
        self.assertIn("prior execution experience", context)
        self.assertIn(f'memory_ref="{record_id}"', context)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT supporting_count,confidence FROM memory_records "
                "WHERE record_id=?",
                (record_id,),
            ) as cur:
                before_completion = await cur.fetchone()
        await complete_usage(record_ids=ids, run_id="target", group_id=7,
                             outcome="completed", input_tokens=90, output_tokens=20, tool_attempts=1)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT state,outcome,input_tokens,tool_attempts FROM experience_usage") as cur:
                row = await cur.fetchone()
            async with db.execute(
                "SELECT supporting_count,confidence FROM memory_records "
                "WHERE record_id=?",
                (record_id,),
            ) as cur:
                evidence = await cur.fetchone()
        self.assertEqual(row, ("injected", "completed", 90, 1))
        self.assertEqual(evidence, before_completion)
        metrics = await collect_learning_shadow_metrics(7)
        self.assertEqual(metrics.experience_completion_without_adoption, 1)
        self.assertEqual(metrics.cases_corrected_success, 1)

    async def test_verified_causal_usage_is_the_only_reinforcement_path(self):
        case_id = await assemble_case(
            run_id="verified-source",
            group_id=7,
            bot_id=3,
            task="repair verified migration",
            outcome="completed",
            tool_records=_corrected_trace("migration failed"),
        )
        record_id = await distill_case(case_id, 7)
        _, ids = await recall_experiences(
            query="repair verified migration",
            run_id="verified-target",
            group_id=7,
            bot_id=3,
        )

        await self._verify_usage(
            UsageKind.EXPERIENCE,
            ids,
            "verified-target",
            UsageState.VERIFIED_SUCCESS,
        )
        # Replaying the same verdict must not reinforce twice.
        await self._verify_usage(
            UsageKind.EXPERIENCE,
            ids,
            "verified-target",
            UsageState.VERIFIED_SUCCESS,
        )

        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT state,adopted_at,executed_at,verified_at,
                    verification_status FROM experience_usage"""
            ) as cur:
                usage = await cur.fetchone()
            async with db.execute(
                "SELECT supporting_count,confidence FROM memory_records "
                "WHERE record_id=?",
                (record_id,),
            ) as cur:
                memory = await cur.fetchone()
        self.assertEqual(usage[0], "verified_success")
        self.assertTrue(all(value is not None for value in usage[1:4]))
        self.assertEqual(usage[4], "verified_success")
        self.assertEqual(memory, (2, 0.76))

    async def test_outcome_evaluator_and_pipeline_are_idempotent(self):
        self.assertFalse(evaluate_outcome(outcome="completed", errors=[], attempts=1).should_distill)
        self.assertFalse(evaluate_outcome(outcome="completed", errors=["x"], attempts=2).should_distill)
        self.assertTrue(evaluate_outcome(
            outcome="completed",
            errors=["x"],
            attempts=3,
            outcome_status="verified_success",
            correction_evidence={"target": "pytest:tests/test_memory.py"},
        ).should_distill)
        case_id = await assemble_case(
            run_id="pipeline", group_id=7, bot_id=3, task="fix migration",
            outcome="completed", tool_records=_corrected_trace(),
        )
        job_id = await process_case(case_id, 7)
        self.assertEqual(job_id, await process_case(case_id, 7))
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT status,attempt FROM pipeline_jobs") as cur:
                queued = await cur.fetchone()
        self.assertEqual(queued, ("pending", 0))

        result = await dispatch_group(7)
        self.assertEqual(result, {"claimed": 1, "completed": 1, "failed": 0})
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT status,attempt,output_json FROM pipeline_jobs") as cur:
                jobs = await cur.fetchall()
            async with db.execute("SELECT COUNT(*) FROM memory_records") as cur:
                count = (await cur.fetchone())[0]
            async with db.execute(
                """SELECT outcome_status,verification_adapter,
                    correction_evidence_json FROM agent_cases"""
            ) as cur:
                case = await cur.fetchone()
        self.assertEqual((jobs[0][0], jobs[0][1]), ("completed", 1))
        self.assertTrue(json.loads(jobs[0][2])["should_distill"])
        self.assertEqual(count, 1)
        self.assertEqual(case[:2], ("verified_success", "pytest"))
        self.assertEqual(
            json.loads(case[2])["target"], "pytest:tests/test_memory.py"
        )

    async def test_pipeline_input_versions_create_distinct_jobs(self):
        case_id = await assemble_case(
            run_id="versioned-pipeline", group_id=7, bot_id=3, task="fix migration",
            outcome="completed", tool_records=[],
        )
        version_one = await process_case(case_id, 7, input_version="1")
        version_two = await process_case(case_id, 7, input_version="2")

        self.assertNotEqual(version_one, version_two)
        result = await dispatch_group(7)
        self.assertEqual(result["completed"], 2)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT input_version,status FROM pipeline_jobs ORDER BY input_version"
            ) as cur:
                rows = await cur.fetchall()
        self.assertEqual(rows, [("1", "completed"), ("2", "completed")])

    async def test_pipeline_promotes_qualified_skill_with_immutable_audit(self):
        for run_id in ("promotion-evidence-1", "promotion-evidence-2"):
            case_id = await assemble_case(
                run_id=run_id, group_id=7, bot_id=3, task="repair schema migration",
                outcome="completed",
                tool_records=_corrected_trace(),
            )
            await process_case(case_id, 7)
            await dispatch_group(7)

        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT skill_id,maturity FROM skills") as cur:
                skill = await cur.fetchone()
            async with db.execute(
                "SELECT skill_id,actor_id,reason,from_maturity,to_maturity "
                "FROM skill_promotion_audit"
            ) as cur:
                audit = await cur.fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                await db.execute("UPDATE skill_promotion_audit SET reason='rewritten'")

        self.assertEqual(skill[1], "active")
        self.assertEqual(audit[0], skill[0])
        self.assertEqual(audit[1], "system:learning_pipeline")
        self.assertEqual(audit[3:], ("trial", "active"))
        self.assertIn("repeated-evidence", audit[2])

    async def test_dispatcher_recovers_expired_lease_and_reports_backlog(self):
        case_id = await assemble_case(
            run_id="expired-pipeline", group_id=7, bot_id=3,
            task="recover expired learning lease", outcome="completed",
            tool_records=[],
        )
        job_id = await process_case(case_id, 7)
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute(
                """UPDATE pipeline_jobs SET status='running',attempt=1,
                   lease_until=?,lease_token='fence:crashed' WHERE job_id=?""",
                (int(time.time() * 1000) - 1, job_id),
            )
            await db.commit()

        before = await job_stats(7)
        self.assertEqual(before["backlog"], 1)
        self.assertEqual(before["expired_lease"], 1)
        result = await dispatch_group(7)
        self.assertEqual(result, {"claimed": 1, "completed": 1, "failed": 0})

        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT status,attempt,lease_token FROM pipeline_jobs WHERE job_id=?",
                (job_id,),
            ) as cur:
                row = await cur.fetchone()
        self.assertEqual(row, ("completed", 2, None))

    async def test_dispatcher_marks_exhausted_crash_lease_dead(self):
        job_id = await process_case("missing-case", 7)
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute(
                """UPDATE pipeline_jobs SET status='running',attempt=max_attempts,
                   lease_until=0,lease_token='fence:crashed' WHERE job_id=?""",
                (job_id,),
            )
            await db.commit()

        result = await dispatch_group(7)
        self.assertEqual(result, {"claimed": 0, "completed": 0, "failed": 0})
        stats = await job_stats(7)
        self.assertEqual(stats["dead"], 1)
        self.assertEqual(stats["expired_lease"], 0)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT status,error,lease_token FROM pipeline_jobs WHERE job_id=?",
                (job_id,),
            ) as cur:
                row = await cur.fetchone()
        self.assertEqual(row[0], "dead")
        self.assertIn("lease expired", row[1])
        self.assertIsNone(row[2])

    async def test_turn_observation_gap_repair_fans_out_durable_stages(self):
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute("INSERT INTO groups(id,name) VALUES(7,'g')")
            await db.execute(
                """INSERT INTO members
                   (id,group_id,name,type,role,model_provider,model_name)
                   VALUES(3,7,'DevBot','bot','developer','claude','opus')"""
            )
            await db.execute(
                """INSERT INTO messages
                   (id,group_id,member_id,content,sender_name,sender_type,
                    sender_provider,sender_model,meta)
                   VALUES(42,7,3,'Use React 19','DevBot','bot','claude','opus',?)""",
                (json.dumps({
                    "memory_observation": {
                        "thread_id": "disc:7:architecture",
                        "run_id": "run:42",
                        "version": "1",
                    }
                }),),
            )
            await db.commit()

        self.assertEqual(await enqueue_missing_turn_observations(7), 1)
        self.assertEqual(await enqueue_missing_turn_observations(7), 0)
        parent = await dispatch_group(7)
        self.assertEqual(parent["completed"], 1)

        with patch("ai.memory.add_to_chroma", new_callable=AsyncMock) as fact, \
             patch("ai.memory.maybe_summarize", new_callable=AsyncMock) as summary, \
             patch("ai.memory.maybe_reflect", new_callable=AsyncMock) as reflection, \
             patch(
                 "ai.tool_events.maybe_compress_tool_events",
                 new_callable=AsyncMock,
             ) as compression:
            reflection.side_effect = RuntimeError("temporary model failure")
            first_children = await dispatch_group(7)
            reflection.side_effect = None
            retry = await dispatch_group(7)

        self.assertEqual(
            first_children, {"claimed": 4, "completed": 3, "failed": 1}
        )
        self.assertEqual(retry, {"claimed": 1, "completed": 1, "failed": 0})
        fact.assert_awaited_once_with(
            42, "Use React 19", "developer", 3, 7,
            "claude", "opus", "disc:7:architecture", strict=True,
        )
        summary.assert_awaited_once_with(
            7, 3, "developer", [3], "disc:7:architecture", strict=True
        )
        reflection.assert_awaited_with(
            7, 3, "developer", "claude", "opus", strict=True
        )
        self.assertEqual(reflection.await_count, 2)
        compression.assert_awaited_once_with(
            7, 3, "developer", "disc:7:architecture", "claude", "opus",
            strict=True,
        )
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                """SELECT job_type,status FROM pipeline_jobs
                   WHERE job_type LIKE 'observe_turn%' ORDER BY job_type"""
            ) as cur:
                rows = await cur.fetchall()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(status == "completed" for _, status in rows))

    async def test_reflexion_is_bounded_and_persists_decision_trace(self):
        self.assertEqual(classify_failure("run_shell", "command failed"), "correctable_execution")
        runner = SimpleNamespace(
            reflexion_used=False, run_id="rr", ctx=SimpleNamespace(group_id=7), bot={"id":3},
            messages=[], tool_records=[{"name":"run_shell","result":"command failed","is_error":True}],
        )
        self.assertTrue(await maybe_inject(runner, iteration=2))
        self.assertFalse(await maybe_inject(runner, iteration=3))
        self.assertIn("Execution Reflexion", runner.messages[-1]["content"])
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT failure_class,step_id FROM run_decisions") as cur:
                rows = await cur.fetchall()
        self.assertEqual(rows, [("correctable_execution", "rr:step:2")])

    async def test_reflexion_never_retries_permission_failure(self):
        runner = SimpleNamespace(
            reflexion_used=False, run_id="rr", ctx=SimpleNamespace(group_id=7), bot={"id":3},
            messages=[], tool_records=[{"name":"write_file","result":"permission denied","is_error":True}],
        )
        self.assertFalse(await maybe_inject(runner, iteration=1))

    async def test_experience_reinforcement_contradiction_and_decay(self):
        first = await assemble_case(run_id="e1", group_id=7, bot_id=3, task="fix db",
                                    outcome="completed", tool_records=_corrected_trace("verification failed x"))
        second = await assemble_case(run_id="e2", group_id=7, bot_id=3, task="fix db",
                                     outcome="completed", tool_records=_corrected_trace("verification failed y"))
        record_id = await distill_case(first, 7)
        self.assertEqual(record_id, await distill_case(second, 7))
        for run_id in ("bad1", "bad2"):
            await recall_experiences(query="fix db", run_id=run_id, group_id=7, bot_id=3)
            await complete_usage(record_ids=[record_id],run_id=run_id,group_id=7,outcome="failed",
                                 input_tokens=1,output_tokens=1,tool_attempts=1)
            await self._verify_usage(
                UsageKind.EXPERIENCE,
                [record_id],
                run_id,
                UsageState.VERIFIED_FAILURE,
            )
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT supporting_count,contradicting_count,status FROM memory_records") as cur:
                row = await cur.fetchone()
        self.assertEqual(row, (2,2,"suspended"))
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute("UPDATE memory_records SET status='active',confidence=0.4,created_at=1,last_used_at=NULL")
            await db.commit()
        self.assertEqual(await decay_experiences(7, now_ms=100*86_400_000), 1)

    async def test_suspended_experience_requires_hysteresis_to_reactivate(self):
        case_ids = []
        for index in range(6):
            case_ids.append(await assemble_case(
                run_id=f"hysteresis-{index}", group_id=7, bot_id=3, task="fix db",
                outcome="completed",
                tool_records=_corrected_trace(),
            ))
        record_id = await distill_case(case_ids[0], 7)
        await distill_case(case_ids[1], 7)
        for run_id in ("contradiction-1", "contradiction-2"):
            await recall_experiences(query="fix db", run_id=run_id, group_id=7, bot_id=3)
            await complete_usage(
                record_ids=[record_id], run_id=run_id, group_id=7, outcome="failed",
                input_tokens=1, output_tokens=1, tool_attempts=1,
            )
            await self._verify_usage(
                UsageKind.EXPERIENCE,
                [record_id],
                run_id,
                UsageState.VERIFIED_FAILURE,
            )

        await distill_case(case_ids[2], 7)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT status,confidence FROM memory_records WHERE record_id=?", (record_id,)
            ) as cur:
                after_one = await cur.fetchone()
        self.assertEqual(after_one[0], "suspended")
        self.assertAlmostEqual(after_one[1], 0.49)

        for case_id in case_ids[3:6]:
            await distill_case(case_id, 7)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute(
                "SELECT status,confidence,supporting_count,contradicting_count "
                "FROM memory_records WHERE record_id=?", (record_id,)
            ) as cur:
                recovered = await cur.fetchone()
        self.assertEqual(recovered[0], "active")
        self.assertAlmostEqual(recovered[1], 0.73)
        self.assertEqual(recovered[2:], (6, 2))

    async def test_candidate_compiler_requires_repeated_evidence_and_is_declarative(self):
        first = await assemble_case(run_id="s1",group_id=7,bot_id=3,task="fix schema",
                                    outcome="completed",tool_records=_corrected_trace("verification failed x"))
        second = await assemble_case(run_id="s2",group_id=7,bot_id=3,task="fix schema",
                                     outcome="completed",tool_records=_corrected_trace("verification failed y"))
        record_id = await distill_case(first,7)
        self.assertIsNone(await compile_candidate(record_id,7))
        await distill_case(second,7)
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute("UPDATE memory_records SET confidence=0.75 WHERE record_id=?",(record_id,))
            await db.commit()
        skill_id = await compile_candidate(record_id,7)
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT maturity,risk_level FROM skills WHERE skill_id=?",(skill_id,)) as cur:
                row = await cur.fetchone()
            async with db.execute("SELECT declaration_json FROM skill_versions WHERE skill_id=?",(skill_id,)) as cur:
                declaration = json.loads((await cur.fetchone())[0])
        self.assertEqual(row,("trial","S0")); self.assertEqual(declaration["allowed_tools"],[])
        with self.assertRaises(ValueError):
            validate_declaration({"risk_level":"S1","trigger":"x","procedure":["x"],"allowed_tools":["run_shell"]})

    async def test_skill_maturity_uses_independent_run_outcomes(self):
        case1=await assemble_case(run_id="k1",group_id=7,bot_id=3,task="repair schema migration",
                                  outcome="completed",tool_records=_corrected_trace("verification failed x"))
        case2=await assemble_case(run_id="k2",group_id=7,bot_id=3,task="repair schema migration",
                                  outcome="completed",tool_records=_corrected_trace("verification failed y"))
        record_id=await distill_case(case1,7); await distill_case(case2,7)
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute("UPDATE memory_records SET confidence=.8 WHERE record_id=?",(record_id,)); await db.commit()
        skill_id=await compile_candidate(record_id,7)
        self.assertTrue(await promote_skill(skill_id, 7, "active"))
        context,ids=await recall_skills(query="repair schema migration",run_id="new-run",group_id=7,bot_id=3)
        self.assertEqual(ids,[skill_id]); self.assertIn("declarative skills",context)
        self.assertIn(f'memory_ref="{skill_id}@v1"', context)
        self.assertEqual(
            await resolve_skill_refs(skill_ids=ids, group_id=7, bot_id=3),
            (f"{skill_id}@v1",),
        )
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute(
                "UPDATE skills SET maturity='trial' WHERE skill_id=?",
                (skill_id,),
            )
            await db.commit()
        self.assertEqual(
            await resolve_skill_refs(skill_ids=ids, group_id=7, bot_id=3),
            (),
        )
        async with database.connect(TEST_DB_PATH) as db:
            await db.execute(
                "UPDATE skills SET maturity='active' WHERE skill_id=?",
                (skill_id,),
            )
            await db.commit()
        with patch("ai.skill_learning.project_skill",new=AsyncMock(return_value="x")):
            await complete_skill_usage(skill_ids=ids,run_id="new-run",group_id=7,outcome="completed")
            await self._verify_usage(
                UsageKind.SKILL,
                ids,
                "new-run",
                UsageState.VERIFIED_SUCCESS,
            )
        async with database.connect(TEST_DB_PATH) as db:
            async with db.execute("SELECT maturity,success_count FROM skills WHERE skill_id=?",(skill_id,)) as cur:
                row=await cur.fetchone()
        self.assertEqual(row,("active",1))
        metrics = await collect_learning_shadow_metrics(7)
        self.assertEqual(metrics.skill_verified_success, 1)
        self.assertEqual(metrics.skill_completion_without_adoption, 0)


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
