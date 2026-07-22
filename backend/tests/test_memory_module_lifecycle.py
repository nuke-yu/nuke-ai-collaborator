"""Behavior tests for the embeddable Memory module lifecycle."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from contextlib import asynccontextmanager
from typing import Any, Mapping
from unittest.mock import AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.infrastructure import DrainResult, ProjectionOutbox
from memory.module import MemoryModule


class _Database:
    @asynccontextmanager
    async def _connection(self):
        yield AsyncMock()

    async def connect(self, table_name: str, group_id: int | None, *, write: bool):
        return self._connection()


class _Delivery:
    async def deliver(self, projection_type: str, payload: Mapping[str, Any]) -> None:
        return None


class _Reconciler:
    def __init__(self) -> None:
        self.groups: list[int] = []

    async def reconcile(self, group_id: int) -> int:
        self.groups.append(group_id)
        return 0


class _Schema:
    def __init__(self) -> None:
        self.groups: list[int] = []

    async def ensure_group(self, group_id: int) -> int:
        self.groups.append(group_id)
        return 1


class _Outbox(ProjectionOutbox):
    def __init__(self) -> None:
        super().__init__(_Database(), _Delivery())
        self.drained: list[int] = []

    async def drain(
        self, group_id: int, *, limit: int = 50, event_id: str | None = None
    ) -> DrainResult:
        self.drained.append(group_id)
        return DrainResult(completed=1)


class _FailingGroupOutbox(_Outbox):
    async def drain(
        self, group_id: int, *, limit: int = 50, event_id: str | None = None
    ) -> DrainResult:
        if group_id == 7:
            raise RuntimeError("projection unavailable")
        return await super().drain(group_id, limit=limit, event_id=event_id)


class MemoryModuleLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_is_explicit_and_storage_is_injected(self) -> None:
        database = _Database()
        outbox = _Outbox()
        reconciler = _Reconciler()
        schema = _Schema()
        module = MemoryModule(database, schema, outbox, reconciler)

        result = await module.reconcile_group(9)

        self.assertIs(module.database, database)
        self.assertEqual(schema.groups, [9])
        self.assertEqual(reconciler.groups, [9])
        self.assertEqual(outbox.drained, [9])
        self.assertEqual(result.completed, 1)

    async def test_standalone_start_and_stop_are_idempotent(self) -> None:
        outbox = _Outbox()
        module = MemoryModule(
            _Database(), _Schema(), outbox, _Reconciler(),
            drain_interval_seconds=0.001
        )
        module.register_group(7)

        await module.start()
        first_task = module._task
        await module.start()
        self.assertIs(module._task, first_task)
        self.assertTrue(module.running)

        for _ in range(20):
            if outbox.drained:
                break
            await asyncio.sleep(0.001)
        self.assertIn(7, outbox.drained)

        await module.stop()
        await module.stop()
        self.assertFalse(module.running)

    async def test_group_registration_is_fail_closed(self) -> None:
        module = MemoryModule(_Database(), _Schema(), _Outbox(), _Reconciler())
        with self.assertRaisesRegex(ValueError, "positive"):
            module.register_group(0)
        with self.assertRaisesRegex(ValueError, "positive"):
            await module.reconcile_group(-1)

    async def test_schema_check_is_cached_until_group_is_unregistered(self) -> None:
        schema = _Schema()
        module = MemoryModule(_Database(), schema, _Outbox(), _Reconciler())

        await module.drain_groups((7,))
        await module.drain_groups((7,))
        self.assertEqual(schema.groups, [7])

        module.unregister_group(7)
        await module.drain_groups((7,))
        self.assertEqual(schema.groups, [7, 7])

    async def test_background_failure_is_isolated_per_group(self) -> None:
        outbox = _FailingGroupOutbox()
        module = MemoryModule(
            _Database(), _Schema(), outbox, _Reconciler(),
            drain_interval_seconds=0.001
        )
        module.register_group(7)
        module.register_group(8)

        await module.start()
        for _ in range(20):
            if 8 in outbox.drained:
                break
            await asyncio.sleep(0.001)
        await module.stop()

        self.assertIn(8, outbox.drained)


if __name__ == "__main__":
    unittest.main()
