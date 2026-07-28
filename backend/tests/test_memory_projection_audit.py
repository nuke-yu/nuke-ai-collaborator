"""Read-only canonical Bot memory ↔ Chroma shadow reconciliation."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import AbstractAsyncContextManager
from typing import Any, Mapping
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from memory.application import BotFactObservationService
from memory.application.projection_audit import (
    BotMemoryProjectionAuditService,
    ProjectionAuditResult,
)
from memory.application.projection_rollout import (
    BotMemoryProjectionRolloutGate,
    ProjectionRolloutState,
)
from memory.contracts import ExtractedFactObservation, IngestBotFactObservations
from memory.domain import MemoryScope
from memory.infrastructure import MemorySchemaManager, ProjectionOutbox
from runtime.lifecycle import LifecycleManager


class _PathDatabase:
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(
        self, table_name: str, group_id: int | None, *, write: bool
    ) -> AbstractAsyncContextManager[Any]:
        return db.connect(self.path)


class _Reader:
    def __init__(
        self,
        *,
        by_id: Mapping[str, Mapping[str, Any]],
        scanned: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.by_id = dict(by_id)
        self.scanned = dict(scanned)
        self.scan_offsets: list[int] = []

    async def read_by_ids(
        self, projection_ids: tuple[str, ...]
    ) -> Mapping[str, Mapping[str, Any]]:
        return {
            projection_id: self.by_id[projection_id]
            for projection_id in projection_ids
            if projection_id in self.by_id
        }

    async def scan_group(
        self, group_id: int, *, limit: int, offset: int = 0
    ) -> Mapping[str, Mapping[str, Any]]:
        self.scan_offsets.append(offset)
        return dict(list(self.scanned.items())[offset:offset + limit])


def _command(group_id: int = 7) -> IngestBotFactObservations:
    return IngestBotFactObservations(
        scope=MemoryScope.bot(
            group_id=group_id,
            bot_id=3,
            actor_id="bot:3",
            thread_id="discussion:9",
        ),
        source_id="message:42",
        facts=(
            ExtractedFactObservation(
                content="API version is 2",
                importance=0.8,
                projection_id=f"fact_3_{group_id}_42_0",
            ),
            ExtractedFactObservation(
                content="release branch is main",
                importance=0.7,
                projection_id=f"fact_3_{group_id}_42_1",
            ),
        ),
        role="developer",
        provider="openai",
        model="gpt-test",
        thread_id="discussion:9",
        observed_at=123_000,
    )


class ProjectionAuditServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_memory_projection_audit.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.outbox = ProjectionOutbox(self.database, AsyncMock())
        self.writer = BotFactObservationService(self.database, self.outbox)
        await self.writer.ingest(_command())
        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT payload_json FROM memory_projection_outbox"
            ) as cursor:
                payloads = [json.loads(row[0]) for row in await cursor.fetchall()]
        self.projected = {
            payload["projection_id"]: {
                "content": payload["content"],
                "metadata": payload["metadata"],
            }
            for payload in payloads
        }

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_reports_match_missing_and_orphan_without_mutation(self) -> None:
        first_id = sorted(self.projected)[0]
        reader = _Reader(
            by_id={first_id: self.projected[first_id]},
            scanned={
                first_id: self.projected[first_id],
                "orphan_projection": {"content": "legacy", "metadata": {}},
            },
        )
        result = await BotMemoryProjectionAuditService(
            self.database, reader, limit=10
        ).audit(7)

        self.assertEqual(result.canonical_total, 2)
        self.assertEqual(result.canonical_sampled, 2)
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.missing, 1)
        self.assertEqual(result.orphaned, 1)
        self.assertEqual(result.outbox_pending, 2)
        self.assertFalse(result.truncated)
        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT COUNT(*) FROM memory_projection_outbox"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 2)

    async def test_reports_content_and_metadata_mismatches_separately(self) -> None:
        ids = sorted(self.projected)
        actual = {
            projection_id: {
                "content": item["content"],
                "metadata": dict(item["metadata"]),
            }
            for projection_id, item in self.projected.items()
        }
        actual[ids[0]]["content"] = "stale content"
        actual[ids[1]]["metadata"]["thread_id"] = "wrong-thread"

        result = await BotMemoryProjectionAuditService(
            self.database,
            _Reader(by_id=actual, scanned=actual),
            limit=10,
        ).audit(7)

        self.assertEqual(result.missing, 0)
        self.assertEqual(result.matched, 0)
        self.assertEqual(result.content_mismatched, 1)
        self.assertEqual(result.metadata_mismatched, 1)

    async def test_pending_projection_tombstones_block_rollout(self) -> None:
        async with db.connect(self.path) as connection:
            await connection.execute(
                """UPDATE memory_projection_outbox
                SET projection_type='bot_memory_vector_delete'
                WHERE event_id=(SELECT event_id FROM memory_projection_outbox LIMIT 1)"""
            )
            await connection.commit()

        result = await BotMemoryProjectionAuditService(
            self.database,
            _Reader(by_id=self.projected, scanned=self.projected),
            limit=10,
        ).audit(7)

        self.assertEqual(result.outbox_pending, 2)

    async def test_bounded_audit_marks_truncation_and_avoids_false_orphans(self) -> None:
        first_id = sorted(self.projected)[0]
        result = await BotMemoryProjectionAuditService(
            self.database,
            _Reader(
                by_id={first_id: self.projected[first_id]},
                scanned={
                    "orphan_projection": {"content": "legacy", "metadata": {}},
                },
            ),
            limit=1,
        ).audit(7)

        self.assertEqual(result.canonical_sampled, 1)
        self.assertTrue(result.truncated)
        self.assertEqual(result.orphaned, 0)

    async def test_rollout_audit_pages_past_sample_limit(self) -> None:
        await self.outbox.drain(7)
        reader = _Reader(by_id=self.projected, scanned=self.projected)

        result = await BotMemoryProjectionAuditService(
            self.database, reader, limit=1
        ).audit_for_rollout(7)

        self.assertEqual(result.canonical_total, 2)
        self.assertEqual(result.canonical_sampled, 2)
        self.assertEqual(result.projected_scanned, 2)
        self.assertEqual(result.matched, 2)
        self.assertEqual(result.orphaned, 0)
        self.assertEqual(result.outbox_pending, 0)
        self.assertFalse(result.truncated)
        self.assertFalse(result.snapshot_changed)
        self.assertEqual(reader.scan_offsets, [0, 1, 2])

    async def test_malformed_canonical_record_is_counted_not_fatal(self) -> None:
        async with db.connect(self.path) as connection:
            await connection.execute(
                """UPDATE memory_records SET evidence_json='{"legacy_projection_id":null}'
                WHERE record_id=(SELECT record_id FROM memory_records LIMIT 1)"""
            )
            await connection.commit()
        result = await BotMemoryProjectionAuditService(
            self.database,
            _Reader(by_id=self.projected, scanned={}),
            limit=10,
        ).audit(7)

        self.assertEqual(result.canonical_total, 2)
        self.assertEqual(result.invalid_canonical, 1)
        self.assertEqual(result.missing, 0)

    async def test_group_scope_excludes_other_group_records(self) -> None:
        await self.writer.ingest(_command(group_id=8))
        result = await BotMemoryProjectionAuditService(
            self.database,
            _Reader(by_id=self.projected, scanned=self.projected),
            limit=10,
        ).audit(7)

        self.assertEqual(result.canonical_total, 2)
        self.assertEqual(result.missing, 0)


class ProjectionAuditLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_publishes_latest_snapshot_and_error_count(self) -> None:
        manager = LifecycleManager()
        manager._active_groups[7] = time.time()
        auditor = AsyncMock()
        auditor.audit.side_effect = [
            RuntimeError("chroma unavailable"),
            ProjectionAuditResult(group_id=7, canonical_total=3, missing=1),
        ]
        rollout = AsyncMock()
        rollout.record_audit.return_value = ProjectionRolloutState(
            group_id=7,
            consecutive_passes=0,
            required_passes=3,
            direct_write_enabled=True,
            last_audit_passed=False,
            last_audited_at=123,
            last_failure_reason="missing",
        )
        rollout.record_failure.return_value = ProjectionRolloutState(
            group_id=7,
            consecutive_passes=0,
            required_passes=3,
            direct_write_enabled=True,
            last_audit_passed=False,
            last_audited_at=122,
            last_failure_reason="audit_error",
        )
        with (
            patch(
                "memory.bootstrap.build_bot_memory_projection_auditor",
                return_value=auditor,
            ),
            patch(
                "memory.bootstrap.build_bot_memory_projection_rollout_gate",
                return_value=rollout,
            ),
        ):
            with self.assertLogs("runtime.lifecycle", level="ERROR"):
                await manager._audit_memory_projections()
            await manager._audit_memory_projections()

        snapshot = manager.stats()["memory_projection_audits"]["7"]
        self.assertEqual(snapshot["canonical_total"], 3)
        self.assertEqual(snapshot["missing"], 1)
        self.assertEqual(snapshot["errors_total"], 1)
        self.assertTrue(snapshot["direct_write_enabled"])
        rollout.record_failure.assert_awaited_once_with(7)
        self.assertGreater(snapshot["last_audited_at"], 0)

    async def test_lifecycle_uses_complete_audit_for_clean_truncated_sample(
        self,
    ) -> None:
        manager = LifecycleManager()
        sample = ProjectionAuditResult(
            group_id=7,
            canonical_total=501,
            canonical_sampled=500,
            projected_scanned=500,
            matched=500,
            truncated=True,
        )
        complete = ProjectionAuditResult(
            group_id=7,
            canonical_total=501,
            canonical_sampled=501,
            projected_scanned=501,
            matched=501,
        )
        auditor = AsyncMock()
        auditor.audit.return_value = sample
        auditor.audit_for_rollout.return_value = complete
        rollout = AsyncMock()
        rollout.record_audit.return_value = ProjectionRolloutState(
            group_id=7,
            consecutive_passes=1,
            required_passes=3,
            direct_write_enabled=True,
            last_audit_passed=True,
            last_audited_at=123,
            last_failure_reason="",
        )
        with (
            patch(
                "memory.bootstrap.build_bot_memory_projection_auditor",
                return_value=auditor,
            ),
            patch(
                "memory.bootstrap.build_bot_memory_projection_rollout_gate",
                return_value=rollout,
            ),
        ):
            await manager._audit_memory_projections((7,))

        auditor.audit_for_rollout.assert_awaited_once_with(7)
        rollout.record_audit.assert_awaited_once_with(complete)
        self.assertTrue(
            manager.stats()["memory_projection_audits"]["7"]["truncated"]
        )


class ProjectionRolloutGateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_memory_projection_rollout.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.gate = BotMemoryProjectionRolloutGate(
            self.database, required_passes=3
        )

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    @staticmethod
    def _passing(group_id: int = 7) -> ProjectionAuditResult:
        return ProjectionAuditResult(
            group_id=group_id,
            canonical_total=2,
            canonical_sampled=2,
            projected_scanned=2,
            matched=2,
        )

    async def test_disables_direct_write_after_consecutive_passes(self) -> None:
        first = await self.gate.record_audit(self._passing())
        second = await self.gate.record_audit(self._passing())
        third = await self.gate.record_audit(self._passing())

        self.assertEqual(first.consecutive_passes, 1)
        self.assertTrue(first.direct_write_enabled)
        self.assertEqual(second.consecutive_passes, 2)
        self.assertTrue(second.direct_write_enabled)
        self.assertEqual(third.consecutive_passes, 3)
        self.assertFalse(third.direct_write_enabled)
        self.assertFalse(await self.gate.direct_write_enabled(7))
        self.assertTrue(await self.gate.direct_write_enabled(8))

    async def test_nonqualifying_audit_resets_streak_and_reopens(self) -> None:
        for _ in range(3):
            await self.gate.record_audit(self._passing())

        state = await self.gate.record_audit(
            ProjectionAuditResult(
                group_id=7,
                canonical_total=2,
                canonical_sampled=2,
                projected_scanned=1,
                matched=1,
                missing=1,
            )
        )

        self.assertEqual(state.consecutive_passes, 0)
        self.assertTrue(state.direct_write_enabled)
        self.assertEqual(state.last_failure_reason, "missing")

    async def test_truncated_and_empty_audits_do_not_qualify(self) -> None:
        truncated = await self.gate.record_audit(
            ProjectionAuditResult(
                group_id=7,
                canonical_total=2,
                canonical_sampled=2,
                projected_scanned=2,
                matched=2,
                truncated=True,
            )
        )
        empty = await self.gate.record_audit(ProjectionAuditResult(group_id=7))

        self.assertEqual(truncated.last_failure_reason, "truncated")
        self.assertEqual(empty.last_failure_reason, "no_canonical_records")
        self.assertTrue(empty.direct_write_enabled)

    async def test_changed_snapshot_does_not_qualify(self) -> None:
        changed = await self.gate.record_audit(
            ProjectionAuditResult(
                group_id=7,
                canonical_total=2,
                canonical_sampled=2,
                projected_scanned=2,
                matched=2,
                snapshot_changed=True,
            )
        )

        self.assertEqual(changed.last_failure_reason, "snapshot_changed")
        self.assertTrue(changed.direct_write_enabled)

    async def test_requires_spaced_audits_and_minimum_observation_window(
        self,
    ) -> None:
        now = [1_000.0]
        gate = BotMemoryProjectionRolloutGate(
            self.database,
            required_passes=3,
            min_observation_seconds=120,
            min_audit_interval_seconds=30,
            clock=lambda: now[0],
        )

        first = await gate.record_audit(self._passing())
        now[0] += 10
        ignored = await gate.record_audit(self._passing())
        now[0] += 20
        second = await gate.record_audit(self._passing())
        now[0] += 30
        third = await gate.record_audit(self._passing())
        now[0] += 60
        observed = await gate.record_audit(self._passing())

        self.assertEqual(first.consecutive_passes, 1)
        self.assertEqual(ignored.consecutive_passes, 1)
        self.assertEqual(second.consecutive_passes, 2)
        self.assertEqual(third.consecutive_passes, 3)
        self.assertTrue(third.direct_write_enabled)
        self.assertFalse(observed.direct_write_enabled)

    async def test_transient_failure_reopens_with_decay_and_cooldown(
        self,
    ) -> None:
        now = [1_000.0]
        gate = BotMemoryProjectionRolloutGate(
            self.database,
            required_passes=3,
            min_audit_interval_seconds=10,
            reopen_cooldown_seconds=60,
            clock=lambda: now[0],
        )
        for _ in range(3):
            await gate.record_audit(self._passing())
            now[0] += 10

        reopened = await gate.record_audit(
            ProjectionAuditResult(
                group_id=7,
                canonical_total=2,
                canonical_sampled=2,
                projected_scanned=2,
                matched=2,
                snapshot_changed=True,
            )
        )
        now[0] += 10
        cooling = await gate.record_audit(self._passing())
        now[0] += 50
        recovered = await gate.record_audit(self._passing())

        self.assertTrue(reopened.direct_write_enabled)
        self.assertEqual(reopened.consecutive_passes, 2)
        self.assertTrue(cooling.direct_write_enabled)
        self.assertFalse(recovered.direct_write_enabled)

    async def test_consistency_failure_resets_qualification_history(self) -> None:
        for _ in range(2):
            await self.gate.record_audit(self._passing())

        failed = await self.gate.record_audit(
            ProjectionAuditResult(
                group_id=7,
                canonical_total=2,
                canonical_sampled=2,
                projected_scanned=1,
                matched=1,
                missing=1,
            )
        )

        self.assertEqual(failed.consecutive_passes, 0)
        self.assertEqual(failed.qualified_since, 0)
        self.assertTrue(failed.direct_write_enabled)


if __name__ == "__main__":
    unittest.main()
