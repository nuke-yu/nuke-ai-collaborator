"""Application operations for the canonical projection outbox."""
from __future__ import annotations

from typing import Any, Mapping

from memory.application.context import require_projection_outbox
from memory.ports import ProjectionDrainResult


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
    await require_projection_outbox().enqueue(
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
) -> ProjectionDrainResult:
    return await require_projection_outbox().drain(
        group_id, limit=limit, event_id=event_id
    )


__all__ = ["drain_projection_outbox", "enqueue_projection"]
