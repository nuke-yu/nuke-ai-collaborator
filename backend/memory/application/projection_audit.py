"""Read-only shadow audit between canonical Bot memory and Chroma."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from memory.ports import BotMemoryProjectionReaderPort, MemoryDatabasePort


@dataclass(frozen=True, slots=True)
class ProjectionAuditResult:
    group_id: int
    canonical_total: int = 0
    canonical_sampled: int = 0
    projected_scanned: int = 0
    matched: int = 0
    missing: int = 0
    content_mismatched: int = 0
    metadata_mismatched: int = 0
    orphaned: int = 0
    invalid_canonical: int = 0
    outbox_pending: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, int | bool]:
        return asdict(self)


class BotMemoryProjectionAuditService:
    """Compare derived Chroma state without mutating either store."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        reader: BotMemoryProjectionReaderPort,
        *,
        limit: int = 500,
    ) -> None:
        if limit < 1:
            raise ValueError("projection audit limit must be positive")
        self._database = database
        self._reader = reader
        self._limit = limit

    async def audit(self, group_id: int) -> ProjectionAuditResult:
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        async with await self._database.connect(
            "memory_records", group_id, write=False
        ) as connection:
            async with connection.execute(
                """SELECT COUNT(*) FROM memory_records
                WHERE group_id=? AND kind IN ('fact','reflection')
                AND owner_type='bot' AND status='provisional'""",
                (group_id,),
            ) as cursor:
                canonical_total = int((await cursor.fetchone())[0])
            async with connection.execute(
                """SELECT record_id,kind,bot_id,content,importance,source_ids,
                    metadata_json,evidence_json,COALESCE(effective_from,created_at)
                FROM memory_records
                WHERE group_id=? AND kind IN ('fact','reflection')
                AND owner_type='bot' AND status='provisional'
                ORDER BY updated_at DESC,record_id LIMIT ?""",
                (group_id, self._limit),
            ) as cursor:
                rows = await cursor.fetchall()
            async with connection.execute(
                """SELECT COUNT(*) FROM memory_projection_outbox
                WHERE group_id=? AND projection_type='bot_memory_vector_upsert'
                AND status!='completed'""",
                (group_id,),
            ) as cursor:
                outbox_pending = int((await cursor.fetchone())[0])

        expected: dict[str, dict[str, Any]] = {}
        invalid = 0
        for row in rows:
            try:
                projection_id, item = expected_bot_memory_projection(group_id, row)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                invalid += 1
                continue
            expected[projection_id] = item

        actual = await self._reader.read_by_ids(tuple(expected))
        matched = missing = content_mismatched = metadata_mismatched = 0
        for projection_id, item in expected.items():
            projected = actual.get(projection_id)
            if projected is None:
                missing += 1
                continue
            content_ok = str(projected.get("content") or "") == item["content"]
            metadata_ok = _metadata_matches(
                item["metadata"],
                projected.get("metadata") or {},
            )
            if not content_ok:
                content_mismatched += 1
            if not metadata_ok:
                metadata_mismatched += 1
            if content_ok and metadata_ok:
                matched += 1

        projected = await self._reader.scan_group(group_id, limit=self._limit)
        truncated = canonical_total > self._limit or len(projected) >= self._limit
        orphaned = 0
        if not truncated:
            orphaned = len(set(projected) - set(expected))
        return ProjectionAuditResult(
            group_id=group_id,
            canonical_total=canonical_total,
            canonical_sampled=len(rows),
            projected_scanned=len(projected),
            matched=matched,
            missing=missing,
            content_mismatched=content_mismatched,
            metadata_mismatched=metadata_mismatched,
            orphaned=orphaned,
            invalid_canonical=invalid,
            outbox_pending=outbox_pending,
            truncated=truncated,
        )


def expected_bot_memory_projection(
    group_id: int, row: tuple[Any, ...]
) -> tuple[str, dict]:
    metadata = json.loads(row[6] or "{}")
    evidence = json.loads(row[7] or "{}")
    if not isinstance(metadata, dict) or not isinstance(evidence, dict):
        raise ValueError("canonical projection metadata and evidence must be objects")
    raw_projection_id = evidence["legacy_projection_id"]
    if not isinstance(raw_projection_id, str) or not raw_projection_id.strip():
        raise ValueError("canonical projection id is required")
    projection_id = raw_projection_id.strip()
    model_info = (
        evidence.get("extracted_by")
        if row[1] == "fact"
        else evidence.get("synthesized_by")
    ) or {}
    if not isinstance(model_info, dict):
        raise ValueError("canonical projection model evidence must be an object")
    projection_metadata: dict[str, Any] = {
        "bot_id": int(row[2]),
        "role": str(metadata.get("role") or ""),
        "timestamp": int(row[8]) / 1000,
        "importance": float(row[4]),
        "mem_type": str(row[1]),
        "thread_id": str(metadata.get("thread_id") or ""),
        "scored_by_model": (
            f"{model_info.get('provider', '')}/{model_info.get('model', '')}"
        ),
        "group_id": group_id,
    }
    if row[1] == "reflection":
        projection_metadata["level"] = int(metadata.get("level") or 1)
        sources = json.loads(row[5] or "[]")
        if not isinstance(sources, list):
            raise ValueError("reflection source_ids must be a JSON list")
        projection_metadata["source_ids"] = ",".join(str(item) for item in sources)
    return projection_id, {
        "content": str(row[3]),
        "metadata": projection_metadata,
        "delete_ids": tuple(
            str(item)
            for item in evidence.get("legacy_conflict_ids", ())
            if str(item)
        ),
    }


def _metadata_matches(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> bool:
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if key == "timestamp":
            try:
                if abs(float(actual_value) - float(expected_value)) > 1.0:
                    return False
            except (TypeError, ValueError):
                return False
        elif key == "importance":
            try:
                if abs(float(actual_value) - float(expected_value)) > 1e-6:
                    return False
            except (TypeError, ValueError):
                return False
        elif actual_value != expected_value:
            return False
    return True
