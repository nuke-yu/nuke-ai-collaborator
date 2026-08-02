"""Unified Timeline projection, filtering, pagination, and group isolation."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from db.schema_split import init_group_db
from observability.event_policy import enrich_event_payload
from observability.timeline import get_group_timeline


class TestGroupTimeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "group.db")
        await init_group_db(self.db_path)
        with db.bind_db(self.db_path):
            async with db.write_connect() as conn:
                await conn.executemany(
                    """INSERT INTO agent_sessions
                       (id,bot_id,group_id,config_json,user_message,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    [
                        ("session-7", 71, 7, "{}", "test", "2026-08-02 10:00:00"),
                        ("session-8", 81, 8, "{}", "other", "2026-08-02 10:00:00"),
                    ],
                )
                await conn.executemany(
                    """INSERT INTO session_events
                       (session_id,event_type,payload,created_at) VALUES (?,?,?,?)""",
                    [
                        (
                            "session-7",
                            "session_start",
                            json.dumps(enrich_event_payload("session_start", {"user_content": "go"})),
                            "2026-08-02 10:00:01",
                        ),
                        (
                            "session-7",
                            "tool_call",
                            json.dumps(enrich_event_payload("tool_call", {
                                "tool_name": "read_file", "arguments": {"path": "README.md"}
                            })),
                            "2026-08-02 10:00:02",
                        ),
                        (
                            "session-7",
                            "permission_requested",
                            json.dumps(enrich_event_payload("permission_requested", {
                                "permission_id": "perm_7", "decision_source": "human_required"
                            })),
                            "2026-08-02 10:00:03",
                        ),
                        (
                            "session-8",
                            "permission_denied",
                            json.dumps(enrich_event_payload("permission_denied", {
                                "permission_id": "perm_other"
                            })),
                            "2026-08-02 10:00:04",
                        ),
                    ],
                )
                envelope = {
                    "schema_version": 1,
                    "event_id": "evt_workflow_7",
                    "occurred_at": 1785664805000,
                    "event_type": "stage_entered",
                    "aggregate": {"type": "workflow", "id": "wf_7"},
                    "context": {
                        "group_id": 7, "workflow_id": "wf_7", "stage_id": "build",
                        "session_id": "session-7",
                    },
                    "actor": {"type": "bot", "id": 71},
                    "payload": {},
                    "policy": enrich_event_payload("stage_entered", {})["_observability"],
                }
                await conn.execute(
                    """INSERT INTO workflow_observations
                       (observation_id,group_id,workflow_id,event_type,stage_id,
                        session_id,envelope_json,occurred_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        "evt_workflow_7", 7, "wf_7", "stage_entered", "build",
                        "session-7", json.dumps(envelope), 1785664805000,
                    ),
                )
                await conn.commit()

    async def asyncTearDown(self):
        await db.aclose_writer(self.db_path)
        self.tempdir.cleanup()

    async def _timeline(self, **kwargs):
        with db.bind_db(self.db_path):
            return await get_group_timeline(7, **kwargs)

    async def test_merges_workflow_session_and_permission_newest_first(self):
        result = await self._timeline()
        self.assertEqual(
            [item["source"] for item in result["items"]],
            ["workflow", "permission", "session"],
        )
        self.assertEqual(result["items"][1]["aggregate"], {"type": "permission", "id": "perm_7"})
        self.assertNotIn("_observability", result["items"][1]["payload"])
        self.assertTrue(all(item["context"]["group_id"] == 7 for item in result["items"]))
        self.assertFalse(result["has_more"])

    async def test_diagnostic_events_are_hidden_by_default_but_queryable(self):
        default = await self._timeline()
        self.assertNotIn("tool_call", [item["event_type"] for item in default["items"]])
        diagnostic = await self._timeline(
            business_significant=False,
            event_classes=("diagnostic",),
        )
        self.assertEqual([item["event_type"] for item in diagnostic["items"]], ["tool_call"])

    async def test_cursor_pagination_has_no_gaps_or_duplicates(self):
        first = await self._timeline(limit=2)
        self.assertTrue(first["has_more"])
        self.assertIsNotNone(first["next_cursor"])
        second = await self._timeline(limit=2, cursor=first["next_cursor"])
        ids = [item["event_id"] for item in first["items"] + second["items"]]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)

    async def test_source_and_correlation_filters(self):
        permission = await self._timeline(sources=("permission",))
        self.assertEqual([item["event_type"] for item in permission["items"]], ["permission_requested"])
        workflow = await self._timeline(workflow_id="wf_7", session_id="session-7")
        self.assertEqual([item["source"] for item in workflow["items"]], ["workflow"])

    async def test_invalid_cursor_and_source_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid timeline cursor"):
            await self._timeline(cursor="not-a-cursor")
        with self.assertRaisesRegex(ValueError, "Invalid timeline source"):
            await self._timeline(sources=("metrics",))
