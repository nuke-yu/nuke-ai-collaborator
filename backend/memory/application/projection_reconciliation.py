"""Canonical repair of derived projection intents."""
from __future__ import annotations

import hashlib
import json
import time

from memory.application.projection_audit import expected_bot_memory_projection
from memory.application.vector_projection import enqueue_bot_memory_projection
from memory.ports import MemoryDatabasePort, ProjectionOutboxPort, ProjectionReconcilerPort


class CanonicalProjectionReconciler(ProjectionReconcilerPort):
    """Re-enqueue projections from canonical records without reading Chroma."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        outbox: ProjectionOutboxPort,
    ) -> None:
        self._database = database
        self._outbox = outbox

    async def reconcile(self, group_id: int) -> int:
        now = int(time.time() * 1000)
        count = 0
        async with await self._database.connect("memory_records", group_id, write=True) as db:
            async with db.execute(
                """SELECT record_id,content,bot_id,confidence,updated_at
                   FROM memory_records
                   WHERE group_id=? AND kind='experience' AND status='active'""",
                (group_id,),
            ) as cur:
                rows = await cur.fetchall()
            for record_id, content, bot_id, confidence, updated_at in rows:
                version = hashlib.sha256(
                    f"{record_id}:{updated_at}:{content}".encode()
                ).hexdigest()
                await self._outbox.enqueue(
                    db,
                    event_id=f"experience-vector:{record_id}",
                    projection_type="experience_vector_upsert",
                    aggregate_id=str(record_id),
                    aggregate_version=version,
                    group_id=group_id,
                    payload={
                        "record_id": str(record_id),
                        "content": str(content),
                        "group_id": group_id,
                        "bot_id": bot_id,
                        "confidence": float(confidence or 0),
                        "timestamp": now / 1000,
                    },
                    now_ms=now,
                )
                count += 1

            async with db.execute(
                """SELECT record_id,kind,bot_id,content,importance,source_ids,
                          metadata_json,evidence_json,COALESCE(effective_from,created_at)
                   FROM memory_records
                   WHERE group_id=? AND kind IN ('fact','reflection')
                     AND owner_type='bot' AND status='provisional'""",
                (group_id,),
            ) as cur:
                bot_rows = await cur.fetchall()
            for row in bot_rows:
                try:
                    projection_id, item = expected_bot_memory_projection(group_id, row)
                    await enqueue_bot_memory_projection(
                        self._outbox,
                        db,
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
                    continue
            await db.commit()
        return count
