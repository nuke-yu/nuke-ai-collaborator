"""Policy-driven observability retention and archival receipts."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from observability.retention import enforce_group_retention
from observability.workflow import record_workflow_observations
from sessions.store import append_event, create_session, update_session_status


class TestObservabilityRetention(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "group.db")
        await db.init_group_db(self.path)

    async def asyncTearDown(self):
        await db.aclose_writer(self.path)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def _age_event(self, event_id: int, days: int) -> None:
        async with db.write_connect(self.path) as conn:
            await conn.execute(
                "UPDATE session_events SET created_at=datetime('now',?) WHERE id=?",
                (f"-{days} days", event_id),
            )
            await conn.commit()

    async def _event_ids(self) -> set[int]:
        async with db.connect(self.path) as conn:
            async with conn.execute("SELECT id FROM session_events") as cur:
                return {int(row[0]) for row in await cur.fetchall()}

    async def test_prunes_only_expired_terminal_events_and_keeps_receipts(self):
        with db.bind_db(self.path):
            await create_session("terminal", 1, 7, {}, "done")
            diagnostic = await append_event(
                "terminal", "internal_cache_tick", {
                    "detail": "secret-body-" + "x" * 9000,
                    "evidence_links": [{
                        "kind": "memory", "ref": "exp:retention",
                        "relation": "injected",
                    }],
                },
            )
            execution_young = await append_event(
                "terminal", "llm_response", {"content": "young"}
            )
            execution_old = await append_event(
                "terminal", "llm_response", {"content": "old"}
            )
            security = await append_event(
                "terminal", "permission_approved", {"permission_id": "perm-keep"}
            )
            group_lifetime = await append_event(
                "terminal", "session_start", {"user_content": "keep"}
            )
            stream_lifetime = await append_event(
                "terminal", "stream_buffer_flushed", {"chunks": 4},
            )
            await update_session_status("terminal", "completed")
            await create_session("active", 1, 7, {}, "running")
            active_diagnostic = await append_event(
                "active", "internal_cache_tick", {"detail": "still needed"}
            )

        for event_id, days in (
            (diagnostic, 15), (execution_young, 15), (execution_old, 91),
            (security, 200), (group_lifetime, 200), (active_diagnostic, 30),
        ):
            await self._age_event(event_id, days)

        with db.bind_db(self.path):
            preview = await enforce_group_retention(7, dry_run=True)
        self.assertEqual(preview["session_events_archived"], 3)
        self.assertEqual(preview["artifacts_deleted"], 1)
        self.assertTrue({diagnostic, execution_old, stream_lifetime} <= await self._event_ids())

        with db.bind_db(self.path):
            result = await enforce_group_retention(7)
        self.assertEqual(result["session_events_archived"], 3)
        remaining = await self._event_ids()
        self.assertNotIn(diagnostic, remaining)
        self.assertNotIn(execution_old, remaining)
        self.assertNotIn(stream_lifetime, remaining)
        self.assertTrue({
            execution_young, security, group_lifetime, active_diagnostic,
        } <= remaining)

        async with db.connect(self.path) as conn:
            async with conn.execute(
                """SELECT event_type,retention,content_sha256
                     FROM observability_retention_archive ORDER BY source_row_id"""
            ) as cur:
                receipts = await cur.fetchall()
            async with conn.execute("SELECT COUNT(*) FROM observation_artifacts") as cur:
                artifact_count = int((await cur.fetchone())[0])
            async with conn.execute("SELECT COUNT(*) FROM session_evidence_links") as cur:
                evidence_count = int((await cur.fetchone())[0])
        self.assertEqual(
            {row[0] for row in receipts},
            {"internal_cache_tick", "llm_response", "stream_buffer_flushed"},
        )
        self.assertEqual(
            {row[1] for row in receipts},
            {"stream_lifetime", "diagnostic_14_days", "execution_90_days"},
        )
        self.assertTrue(all(len(row[2]) == 64 for row in receipts))
        self.assertEqual(artifact_count, 0)
        self.assertEqual(evidence_count, 0)
        with db.bind_db(self.path):
            rerun = await enforce_group_retention(7)
        self.assertEqual(rerun["session_events_archived"], 0)

    async def test_model_request_lifecycle_and_ledger_expire_together(self):
        with db.bind_db(self.path):
            await create_session("usage", 1, 7, {}, "bill")
            started = await append_event("usage", "model_request_started", {
                "request_id": "req-old", "request_ordinal": 1,
                "provider": "deepseek", "model": "deepseek-chat",
            })
            completed = await append_event("usage", "model_request_completed", {
                "request_id": "req-old", "provider": "deepseek",
                "model": "deepseek-chat", "input_tokens": 10, "output_tokens": 2,
            })
            await update_session_status("usage", "completed")
        await self._age_event(started, 91)
        await self._age_event(completed, 91)
        async with db.write_connect(self.path) as conn:
            await conn.execute(
                """UPDATE model_usage_ledger
                      SET started_at=datetime('now','-91 days'),
                          completed_at=datetime('now','-91 days')
                    WHERE request_id='req-old'"""
            )
            await conn.commit()

        with db.bind_db(self.path):
            result = await enforce_group_retention(7)
        self.assertEqual(result["model_requests_deleted"], 1)
        self.assertEqual(result["session_events_archived"], 2)
        self.assertFalse({started, completed} & await self._event_ids())
        async with db.connect(self.path) as conn:
            async with conn.execute("SELECT COUNT(*) FROM model_usage_ledger") as cur:
                self.assertEqual((await cur.fetchone())[0], 0)

    async def test_workflow_policy_is_enforced_without_pruning_group_lifetime(self):
        now_ms = int(time.time() * 1000)
        with db.bind_db(self.path):
            await record_workflow_observations(7, "test", [{
                "event_type": "internal_workflow_tick", "workflow_id": "wf-old",
                "occurred_at": now_ms - 15 * 24 * 3600 * 1000,
                "payload": {"detail": "x" * 9000},
            }, {
                "event_type": "workflow_started", "workflow_id": "wf-keep",
                "occurred_at": now_ms - 200 * 24 * 3600 * 1000,
                "payload": {},
            }])
            result = await enforce_group_retention(7, now=now_ms / 1000)
        self.assertEqual(result["workflow_observations_archived"], 1)
        self.assertEqual(result["artifacts_deleted"], 1)
        async with db.connect(self.path) as conn:
            async with conn.execute(
                "SELECT event_type FROM workflow_observations ORDER BY id"
            ) as cur:
                remaining = [row[0] for row in await cur.fetchall()]
        self.assertEqual(remaining, ["workflow_started"])


if __name__ == "__main__":
    unittest.main()
