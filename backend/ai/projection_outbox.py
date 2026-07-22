"""Compatibility facade for the Memory module's projection outbox."""
from __future__ import annotations

from typing import Any, Mapping

from memory.bootstrap import get_memory_module
from memory.infrastructure import DrainResult


async def enqueue_projection(
    db: Any,
    *,
    event_id: str,
    projection_type: str,
    aggregate_id: str,
    aggregate_version: str,
    group_id: int,
    payload: Mapping[str, Any],
    now_ms: int | None = None,
) -> None:
    await get_memory_module().projection_outbox.enqueue(
        db,
        event_id=event_id,
        projection_type=projection_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        group_id=group_id,
        payload=payload,
        now_ms=now_ms,
    )


async def drain_projection_outbox(
    group_id: int, *, limit: int = 50, event_id: str | None = None
) -> DrainResult:
    return await get_memory_module().projection_outbox.drain(
        group_id, limit=limit, event_id=event_id
    )


__all__ = ["DrainResult", "drain_projection_outbox", "enqueue_projection"]
