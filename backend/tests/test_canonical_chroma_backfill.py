"""Historical Chroma Fact/Reflection → canonical SQLite backfill."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import AbstractAsyncContextManager
from typing import Any, Mapping
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from ai import memory
from memory.application import CanonicalChromaBackfillService
from memory.infrastructure import MemorySchemaManager
from scripts import backfill_canonical_bot_memory as cli


class _PathDatabase:
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(
        self, table_name: str, group_id: int | None, *, write: bool
    ) -> AbstractAsyncContextManager[Any]:
        return db.connect(self.path)


class _PagedReader:
    def __init__(self, items: Mapping[str, Mapping[str, Any]]) -> None:
        self.items = list(items.items())
        self.offsets: list[int] = []

    async def read_by_ids(self, projection_ids):
        return {}

    async def scan_group(
        self, group_id: int, *, limit: int, offset: int = 0
    ) -> Mapping[str, Mapping[str, Any]]:
        self.offsets.append(offset)
        return dict(self.items[offset:offset + limit])


def _items() -> dict[str, dict]:
    base = {
        "group_id": 7,
        "bot_id": 3,
        "role": "developer",
        "timestamp": 123.0,
        "importance": 0.8,
        "thread_id": "discussion:9",
        "scored_by_model": "openai/gpt-test",
    }
    return {
        "fact_3_7_42_0": {
            "content": "API version is 2",
            "metadata": {**base, "mem_type": "fact"},
        },
        "legacy_9_0": {
            "content": "legacy fact with old id",
            "metadata": {
                **base,
                "bot_id": 4,
                "importance": 0.6,
            },
        },
        "refl_3_7_123001_0_0": {
            "content": "configuration drift causes deploy failures",
            "metadata": {
                **base,
                "mem_type": "reflection",
                "importance": 0.9,
                "level": 1,
                "source_ids": "fact_3_7_40_0,fact_3_7_41_0",
            },
        },
        "invalid_wrong_group": {
            "content": "must never cross groups",
            "metadata": {**base, "group_id": 8, "mem_type": "fact"},
        },
        "invalid_no_timestamp": {
            "content": "missing temporal evidence",
            "metadata": {
                key: value for key, value in base.items() if key != "timestamp"
            },
        },
    }


class CanonicalChromaBackfillServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_chroma_backfill.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)
        self.reader = _PagedReader(_items())
        self.service = CanonicalChromaBackfillService(
            self.database,
            self.reader,
            lambda content: content.replace("API", "[SAFE]"),
        )

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_dry_run_reports_plan_and_writes_nothing(self) -> None:
        report = await self.service.backfill(7, dry_run=True, batch_size=2)

        self.assertEqual(report.scanned, 5)
        self.assertEqual(report.eligible, 3)
        self.assertEqual(report.would_insert, 3)
        self.assertEqual(report.inserted, 0)
        self.assertEqual(report.invalid, 2)
        self.assertEqual((report.facts, report.reflections), (2, 1))
        self.assertEqual(self.reader.offsets, [0, 2, 4])
        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT COUNT(*) FROM memory_records"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_apply_is_idempotent_and_preserves_canonical_semantics(self) -> None:
        first = await self.service.backfill(7, dry_run=False, batch_size=2)
        second = await self.service.backfill(7, dry_run=False, batch_size=2)

        self.assertEqual((first.inserted, first.existing), (3, 0))
        self.assertEqual((second.inserted, second.existing), (0, 3))
        async with db.connect(self.path) as connection:
            async with connection.execute(
                """SELECT kind,bot_id,status,content,owner_type,authority,
                    sensitivity,evidence_json,metadata_json,effective_from
                FROM memory_records ORDER BY kind,bot_id"""
            ) as cursor:
                rows = await cursor.fetchall()
            async with connection.execute(
                "SELECT COUNT(*) FROM memory_projection_outbox"
            ) as cursor:
                outbox_count = (await cursor.fetchone())[0]
        self.assertEqual(len(rows), 3)
        self.assertEqual(outbox_count, 0)
        self.assertEqual(
            {(row[0], row[5]) for row in rows},
            {
                ("fact", "bot_observation"),
                ("reflection", "bot_inference"),
            },
        )
        for row in rows:
            self.assertEqual(row[2], "provisional")
            self.assertEqual(row[4], "bot")
            self.assertEqual(row[6], "group")
            self.assertIn('"source_type": "legacy_chroma_backfill"', row[7])
            self.assertIn('"projection_state": "legacy_chroma_backfilled"', row[8])
            self.assertEqual(row[9], 123_000)
        self.assertTrue(any("[SAFE] version is 2" == row[3] for row in rows))

    async def test_rerun_does_not_reactivate_rejected_record(self) -> None:
        await self.service.backfill(7, dry_run=False)
        async with db.connect(self.path) as connection:
            await connection.execute(
                """UPDATE memory_records SET status='rejected'
                WHERE record_id=(SELECT record_id FROM memory_records LIMIT 1)"""
            )
            await connection.commit()

        report = await self.service.backfill(7, dry_run=False)

        self.assertEqual((report.inserted, report.existing), (0, 3))
        async with db.connect(self.path) as connection:
            async with connection.execute(
                "SELECT COUNT(*) FROM memory_records WHERE status='rejected'"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_bot_filter_and_group_validation_are_fail_closed(self) -> None:
        report = await self.service.backfill(
            7,
            dry_run=True,
            bot_ids=frozenset({3}),
        )

        self.assertEqual(report.eligible, 2)
        self.assertEqual(report.filtered_bot, 1)
        self.assertEqual(report.invalid, 2)
        self.assertEqual((report.facts, report.reflections), (1, 1))


class CanonicalChromaBackfillCliTest(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_does_not_initialize_or_mutate_schema(self) -> None:
        report = MagicMock()
        report.as_dict.return_value = {
            "group_id": 7,
            "dry_run": True,
            "would_insert": 2,
        }
        client = AsyncMock()
        client.backfill.return_value = report
        module = AsyncMock()
        with (
            patch.object(cli.os.path, "isfile", return_value=True),
            patch.object(
                cli,
                "build_canonical_chroma_backfill_client",
                return_value=client,
            ),
            patch.object(cli, "get_memory_module", return_value=module),
        ):
            reports = await cli.run_backfill(group_ids=(7,), apply=False)

        self.assertEqual(reports[0]["would_insert"], 2)
        module.ensure_group.assert_not_awaited()
        client.backfill.assert_awaited_once()
        self.assertTrue(client.backfill.await_args.kwargs["dry_run"])

    async def test_apply_initializes_only_explicit_existing_groups(self) -> None:
        report = MagicMock()
        report.as_dict.return_value = {
            "group_id": 7,
            "dry_run": False,
            "inserted": 2,
        }
        client = AsyncMock()
        client.backfill.return_value = report
        module = AsyncMock()
        with (
            patch.object(
                cli,
                "group_db_path",
                side_effect=lambda group_id: f"group_{group_id}.db",
            ),
            patch.object(
                cli.os.path,
                "isfile",
                side_effect=lambda path: path.endswith("group_7.db"),
            ),
            patch.object(
                cli,
                "build_canonical_chroma_backfill_client",
                return_value=client,
            ),
            patch.object(cli, "get_memory_module", return_value=module),
        ):
            reports = await cli.run_backfill(
                group_ids=(7, 8),
                apply=True,
            )

        module.ensure_group.assert_awaited_once_with(7)
        client.backfill.assert_awaited_once()
        self.assertEqual(reports[1]["skipped"], "group_db_missing")


class LegacyChromaBackfillScanTest(unittest.TestCase):
    @patch("ai.memory._get_collection")
    def test_scan_includes_untyped_legacy_facts_but_excludes_other_kinds(
        self, get_collection
    ) -> None:
        collection = MagicMock()
        get_collection.return_value = collection
        collection.get.return_value = {}

        memory.ChromaStore.get_group_bot_memories_sync(7, 50, 100)

        kwargs = collection.get.call_args.kwargs
        self.assertEqual((kwargs["limit"], kwargs["offset"]), (50, 100))
        where_text = str(kwargs["where"])
        self.assertIn("'reflection'", where_text)
        self.assertIn("'tool_episode'", where_text)
        self.assertIn("'experience'", where_text)
        self.assertIn("'$ne'", where_text)


if __name__ == "__main__":
    unittest.main()
