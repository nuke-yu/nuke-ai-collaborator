"""Embeddable lifecycle boundary for the Memory bounded context."""
from __future__ import annotations

import asyncio
import logging

from memory.infrastructure import DrainResult, ProjectionOutbox
from memory.ports import MemoryDatabasePort, MemorySchemaPort, ProjectionReconcilerPort

log = logging.getLogger(__name__)


class MemoryModule:
    """Own Memory background work independently of FastAPI and Worker runtime."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        schema: MemorySchemaPort,
        projection_outbox: ProjectionOutbox,
        reconciler: ProjectionReconcilerPort,
        *,
        drain_interval_seconds: float = 60.0,
    ) -> None:
        if drain_interval_seconds <= 0:
            raise ValueError("drain_interval_seconds must be positive")
        self.database = database
        self.schema = schema
        self.projection_outbox = projection_outbox
        self._reconciler = reconciler
        self._drain_interval_seconds = drain_interval_seconds
        self._groups: set[int] = set()
        self._schema_versions: dict[int, int] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def reconciler(self) -> ProjectionReconcilerPort:
        return self._reconciler

    def register_group(self, group_id: int) -> None:
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        self._groups.add(group_id)

    def unregister_group(self, group_id: int) -> None:
        self._groups.discard(group_id)
        self._schema_versions.pop(group_id, None)

    async def ensure_group(self, group_id: int) -> int:
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        if group_id not in self._schema_versions:
            version = await self.schema.ensure_group(group_id)
            self._schema_versions[group_id] = version
        return self._schema_versions[group_id]

    async def reconcile_group(self, group_id: int) -> DrainResult:
        """Rebuild durable intents and immediately deliver ready projections."""
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        await self.ensure_group(group_id)
        await self._reconciler.reconcile(group_id)
        return await self.projection_outbox.drain(group_id)

    async def drain_groups(
        self, group_ids: tuple[int, ...] | None = None
    ) -> dict[int, DrainResult]:
        targets = group_ids if group_ids is not None else tuple(self._groups)
        results: dict[int, DrainResult] = {}
        for group_id in targets:
            await self.ensure_group(group_id)
            results[group_id] = await self.projection_outbox.drain(group_id)
        return results

    async def start(self) -> None:
        """Start the optional standalone reconciliation loop."""
        if self.running:
            return
        self._task = asyncio.create_task(
            self._run(), name="memory-projection-reconciler"
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._drain_interval_seconds)
            except asyncio.CancelledError:
                raise
            for group_id in tuple(self._groups):
                try:
                    await self.ensure_group(group_id)
                    await self.projection_outbox.drain(group_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception(
                        "memory: projection reconciliation failed for group %d",
                        group_id,
                    )
