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
from memory.adapters.runtime.sqlite_legacy import legacy_memory_database

log = logging.getLogger(__name__)


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
                metadata = json.loads(row[6] or "{}")
                evidence = json.loads(row[7] or "{}")
                projection_id = str(evidence["legacy_projection_id"])
                model_info = (
                    evidence.get("extracted_by")
                    if row[1] == "fact"
                    else evidence.get("synthesized_by")
                ) or {}
                projection_metadata = {
                    "bot_id": int(row[2]),
                    "role": str(metadata.get("role") or ""),
                    "timestamp": int(row[8]) / 1000,
                    "importance": float(row[4]),
                    "mem_type": str(row[1]),
                    "thread_id": str(metadata.get("thread_id") or ""),
                    "scored_by_model": (
                        f"{model_info.get('provider', '')}/"
                        f"{model_info.get('model', '')}"
                    ),
                    "group_id": group_id,
                }
                if row[1] == "reflection":
                    projection_metadata["level"] = int(metadata.get("level") or 1)
                    projection_metadata["source_ids"] = ",".join(
                        str(item) for item in json.loads(row[5] or "[]")
                    )
                await enqueue_bot_memory_projection(
                    get_memory_module().projection_outbox,
                    connection,
                    record_id=str(row[0]),
                    group_id=group_id,
                    projection_id=projection_id,
                    content=str(row[3]),
                    metadata=projection_metadata,
                    delete_ids=tuple(
                        str(item)
                        for item in evidence.get("legacy_conflict_ids", ())
                        if str(item)
                    ),
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


def redact_projection_error(message: str) -> str:
    """Use the host's secret redactor without coupling the outbox engine to it."""
    from executors.redaction import redact_secrets

    redacted, _ = redact_secrets(message)
    return redacted
