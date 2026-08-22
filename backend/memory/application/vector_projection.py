"""Stable projection intents for canonical Bot facts and reflections."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from memory.contracts.projection import BOT_MEMORY_VECTOR_DELETE, BOT_MEMORY_VECTOR_UPSERT
from memory.ports import ProjectionOutboxPort


async def enqueue_bot_memory_projection(
    outbox: ProjectionOutboxPort,
    connection: Any,
    *,
    record_id: str,
    group_id: int,
    projection_id: str,
    content: str,
    metadata: Mapping[str, Any],
    delete_ids: tuple[str, ...] = (),
    now_ms: int,
) -> None:
    payload = {
        "projection_id": projection_id,
        "content": content,
        "metadata": dict(metadata),
        "delete_ids": list(delete_ids),
    }
    await outbox.enqueue(
        connection,
        event_id=bot_memory_projection_event_id(record_id),
        projection_type=BOT_MEMORY_VECTOR_UPSERT,
        aggregate_id=record_id,
        aggregate_version=_projection_version(payload),
        group_id=group_id,
        payload=payload,
        now_ms=now_ms,
    )


def bot_memory_projection_event_id(record_id: str) -> str:
    return f"bot-memory-vector:{record_id}"


async def enqueue_bot_memory_projection_delete(
    outbox: ProjectionOutboxPort,
    connection: Any,
    *,
    record_id: str,
    group_id: int,
    projection_id: str,
    now_ms: int,
) -> None:
    """Replace any in-flight upsert for a superseded record with a tombstone."""
    payload = {"projection_id": projection_id}
    await outbox.enqueue(
        connection,
        event_id=bot_memory_projection_event_id(record_id),
        projection_type=BOT_MEMORY_VECTOR_DELETE,
        aggregate_id=record_id,
        aggregate_version=_projection_version(payload),
        group_id=group_id,
        payload=payload,
        now_ms=now_ms,
    )


def _projection_version(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
