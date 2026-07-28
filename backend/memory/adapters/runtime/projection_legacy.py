"""Projection adapters backed by the application's existing AI implementations."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Mapping

from memory.application.vector_projection import (
    BOT_MEMORY_VECTOR_UPSERT,
    enqueue_bot_memory_projection,
)
from memory.application.projection_audit import expected_bot_memory_projection
from memory.adapters.runtime.sqlite_legacy import legacy_memory_database

log = logging.getLogger(__name__)


class LegacyBotMemoryProjectionReader:
    async def read_by_ids(
        self, projection_ids: tuple[str, ...]
    ) -> Mapping[str, Mapping[str, Any]]:
        if not projection_ids:
            return {}
        from ai.memory import ChromaStore

        result = await asyncio.to_thread(
            ChromaStore.get_by_ids_sync,
            list(projection_ids),
        )
        return _projection_items(result)

    async def scan_group(
        self, group_id: int, *, limit: int
    ) -> Mapping[str, Mapping[str, Any]]:
        from ai.memory import ChromaStore

        result = await asyncio.to_thread(
            ChromaStore.get_group_bot_memories_sync,
            group_id,
            limit,
        )
        return _projection_items(result)


class LegacyMemoryProjectionDelivery:
    async def deliver(
        self, projection_type: str, payload: Mapping[str, Any]
    ) -> None:
        if projection_type == "experience_vector_upsert":
            from ai.experiences import _index_vector

            await _index_vector(
                str(payload["record_id"]),
                str(payload["content"]),
                int(payload["group_id"]),
                int(payload["bot_id"]) if payload.get("bot_id") is not None else None,
                float(payload["confidence"]),
            )
            return
        if projection_type == BOT_MEMORY_VECTOR_UPSERT:
            from ai.memory import ChromaStore

            await asyncio.to_thread(
                ChromaStore.write_fact_sync,
                str(payload["projection_id"]),
                str(payload["content"]),
                dict(payload["metadata"]),
            )
            delete_ids = [
                str(item) for item in payload.get("delete_ids", ()) if str(item)
            ]
            if delete_ids:
                await asyncio.to_thread(ChromaStore.delete_ids_sync, delete_ids)
            return
        raise ValueError(f"unsupported memory projection type: {projection_type}")


class LegacyMemoryProjectionReconciler:
    async def reconcile(self, group_id: int) -> int:
        from ai.experiences import reconcile_experience_projections

        experiences = await reconcile_experience_projections(group_id)
        bot_memories = await _reconcile_bot_memory_projections(group_id)
        return experiences + bot_memories


async def _reconcile_bot_memory_projections(group_id: int) -> int:
    """Rebuild Fact/Reflection intents from canonical group-local records."""
    from memory.bootstrap import get_memory_module

    now = int(time.time() * 1000)
    count = 0
    async with await legacy_memory_database.connect(
        "memory_records", group_id, write=True
    ) as connection:
        async with connection.execute(
            """SELECT record_id,kind,bot_id,content,importance,source_ids,
                metadata_json,evidence_json,COALESCE(effective_from,created_at)
            FROM memory_records
            WHERE group_id=? AND kind IN ('fact','reflection')
            AND owner_type='bot' AND status='provisional'""",
            (group_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            try:
                projection_id, item = expected_bot_memory_projection(group_id, row)
                await enqueue_bot_memory_projection(
                    get_memory_module().projection_outbox,
                    connection,
                    record_id=str(row[0]),
                    group_id=group_id,
                    projection_id=projection_id,
                    content=item["content"],
                    metadata=item["metadata"],
                    delete_ids=item["delete_ids"],
                    now_ms=now,
                )
                count += 1
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                log.warning(
                    "memory: skipped malformed canonical projection record %s",
                    row[0],
                )
        await connection.commit()
    return count


# Compatibility names retained for callers and tests during module extraction.
LegacyExperienceProjectionDelivery = LegacyMemoryProjectionDelivery
LegacyExperienceProjectionReconciler = LegacyMemoryProjectionReconciler


def _projection_items(result: Mapping[str, Any] | None) -> dict[str, dict]:
    ids = (result or {}).get("ids") or []
    documents = (result or {}).get("documents") or []
    metadatas = (result or {}).get("metadatas") or []
    return {
        str(projection_id): {
            "content": str(documents[index]) if index < len(documents) else "",
            "metadata": (
                dict(metadatas[index])
                if index < len(metadatas) and metadatas[index]
                else {}
            ),
        }
        for index, projection_id in enumerate(ids)
    }


def redact_projection_error(message: str) -> str:
    """Use the host's secret redactor without coupling the outbox engine to it."""
    from executors.redaction import redact_secrets

    redacted, _ = redact_secrets(message)
    return redacted
