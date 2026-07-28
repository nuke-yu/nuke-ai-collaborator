"""Idempotent legacy Chroma Fact/Reflection backfill into canonical SQLite."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from memory.ports import BotMemoryProjectionReaderPort, MemoryDatabasePort

from .bot_facts import bot_fact_record_id
from .reflections import bot_reflection_record_id

_SCOPED_FACT_ID = re.compile(
    r"^fact_(?P<bot_id>\d+)_(?P<group_id>\d+)_(?P<message_id>\d+)_(?P<index>\d+)$"
)


@dataclass(frozen=True, slots=True)
class ChromaBackfillReport:
    group_id: int
    dry_run: bool
    scanned: int = 0
    eligible: int = 0
    would_insert: int = 0
    inserted: int = 0
    existing: int = 0
    invalid: int = 0
    filtered_bot: int = 0
    facts: int = 0
    reflections: int = 0

    def as_dict(self) -> dict[str, int | bool]:
        return asdict(self)


class CanonicalChromaBackfillService:
    """Import legacy derived memories without changing their Chroma projection."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        reader: BotMemoryProjectionReaderPort,
        content_sanitizer: Callable[[str], str],
    ) -> None:
        self._database = database
        self._reader = reader
        self._content_sanitizer = content_sanitizer

    async def backfill(
        self,
        group_id: int,
        *,
        dry_run: bool = True,
        bot_ids: frozenset[int] = frozenset(),
        batch_size: int = 500,
    ) -> ChromaBackfillReport:
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if any(bot_id <= 0 for bot_id in bot_ids):
            raise ValueError("bot_ids must contain positive integers")

        scanned = eligible = invalid = filtered_bot = facts = reflections = 0
        would_insert = inserted = existing = 0
        offset = 0
        while True:
            page = await self._reader.scan_group(
                group_id,
                limit=batch_size,
                offset=offset,
            )
            if not page:
                break
            scanned += len(page)
            candidates: list[dict[str, Any]] = []
            for projection_id, item in page.items():
                try:
                    candidate = _candidate(group_id, projection_id, item)
                    candidate["content"] = self._content_sanitizer(
                        candidate["content"]
                    )
                except (KeyError, TypeError, ValueError):
                    invalid += 1
                    continue
                if bot_ids and candidate["bot_id"] not in bot_ids:
                    filtered_bot += 1
                    continue
                candidates.append(candidate)
                if candidate["kind"] == "fact":
                    facts += 1
                else:
                    reflections += 1
            eligible += len(candidates)
            existing_ids: set[str] = set()
            async with await self._database.connect(
                "memory_records", group_id, write=not dry_run
            ) as connection:
                for start in range(0, len(candidates), 400):
                    lookup_batch = candidates[start:start + 400]
                    placeholders = ",".join("?" for _ in lookup_batch)
                    async with connection.execute(
                        f"""SELECT record_id FROM memory_records
                        WHERE group_id=? AND record_id IN ({placeholders})""",
                        (group_id, *(
                            item["record_id"] for item in lookup_batch
                        )),
                    ) as cursor:
                        existing_ids.update(
                            str(row[0]) for row in await cursor.fetchall()
                        )
                pending = [
                    candidate
                    for candidate in candidates
                    if candidate["record_id"] not in existing_ids
                ]
                existing += len(existing_ids)
                if dry_run:
                    would_insert += len(pending)
                else:
                    now = int(time.time() * 1000)
                    for candidate in pending:
                        cursor = await connection.execute(
                            """INSERT OR IGNORE INTO memory_records
                            (record_id,kind,group_id,bot_id,status,content,
                             task_signature,confidence,importance,source_ids,
                             metadata_json,algorithm_version,owner_type,authority,
                             subject_key,sensitivity,evidence_json,created_by,
                             effective_from,created_at,updated_at)
                            VALUES (?,?,?,?, 'provisional',?,'',?,?,?,?,
                                'legacy-chroma-backfill-v1','bot',?,'','group',?,
                                'system:memory-backfill',?,?,?)""",
                            (
                                candidate["record_id"],
                                candidate["kind"],
                                group_id,
                                candidate["bot_id"],
                                candidate["content"],
                                candidate["confidence"],
                                candidate["importance"],
                                json.dumps(candidate["source_ids"]),
                                json.dumps(
                                    candidate["metadata"],
                                    ensure_ascii=False,
                                    sort_keys=True,
                                ),
                                candidate["authority"],
                                json.dumps(
                                    candidate["evidence"],
                                    ensure_ascii=False,
                                    sort_keys=True,
                                ),
                                candidate["effective_from"],
                                now,
                                now,
                            ),
                        )
                        inserted += max(0, cursor.rowcount)
                    await connection.commit()
            if len(page) < batch_size:
                break
            offset += len(page)

        return ChromaBackfillReport(
            group_id=group_id,
            dry_run=dry_run,
            scanned=scanned,
            eligible=eligible,
            would_insert=would_insert,
            inserted=inserted,
            existing=existing,
            invalid=invalid,
            filtered_bot=filtered_bot,
            facts=facts,
            reflections=reflections,
        )


def _candidate(
    group_id: int,
    projection_id: str,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    projection_id = str(projection_id).strip()
    content = str(item.get("content") or "").strip()
    metadata = item.get("metadata")
    if not projection_id or not content or not isinstance(metadata, Mapping):
        raise ValueError("projection id, content, and metadata are required")
    if int(metadata.get("group_id")) != group_id:
        raise ValueError("projection group does not match target group")
    bot_id = int(metadata.get("bot_id"))
    if bot_id <= 0:
        raise ValueError("projection bot_id must be positive")
    kind = str(metadata.get("mem_type") or "fact")
    if kind not in ("fact", "reflection"):
        raise ValueError("unsupported projection memory type")
    timestamp = float(metadata.get("timestamp"))
    importance = float(metadata.get("importance", 0.5))
    if timestamp < 0 or not 0.0 <= importance <= 1.0:
        raise ValueError("invalid projection timestamp or importance")
    scored_by = str(metadata.get("scored_by_model") or "legacy/unknown")
    provider, separator, model = scored_by.partition("/")
    if not separator:
        provider, model = "legacy", scored_by

    if kind == "fact":
        match = _SCOPED_FACT_ID.fullmatch(projection_id)
        if (
            match
            and int(match.group("group_id")) == group_id
            and int(match.group("bot_id")) == bot_id
        ):
            source_id = f"message:{match.group('message_id')}"
        else:
            source_id = f"legacy-chroma:{projection_id}"
        record_id = bot_fact_record_id(
            group_id, bot_id, source_id, projection_id
        )
        source_ids = [source_id]
        confidence = 0.5
        authority = "bot_observation"
        evidence = {
            "source_type": "legacy_chroma_backfill",
            "source_id": source_id,
            "legacy_projection_id": projection_id,
            "legacy_conflict_ids": [],
            "extracted_by": {"provider": provider, "model": model},
        }
        canonical_metadata = {
            "schema_version": "bot-fact-observation-v1",
            "role": str(metadata.get("role") or ""),
            "thread_id": str(metadata.get("thread_id") or ""),
            "projection_state": "legacy_chroma_backfilled",
        }
    else:
        sources = tuple(
            source.strip()
            for source in str(metadata.get("source_ids") or "").split(",")
            if source.strip()
        )
        if not sources:
            raise ValueError("reflection projection requires source_ids")
        record_id = bot_reflection_record_id(group_id, bot_id, projection_id)
        source_ids = list(dict.fromkeys(sources))
        confidence = 0.4
        authority = "bot_inference"
        evidence = {
            "source_type": "legacy_chroma_backfill",
            "legacy_projection_id": projection_id,
            "legacy_source_projection_ids": source_ids,
            "legacy_conflict_ids": [],
            "synthesized_by": {"provider": provider, "model": model},
        }
        canonical_metadata = {
            "schema_version": "bot-reflection-v1",
            "role": str(metadata.get("role") or ""),
            "thread_id": str(metadata.get("thread_id") or ""),
            "level": max(1, int(metadata.get("level") or 1)),
            "projection_state": "legacy_chroma_backfilled",
        }
    return {
        "record_id": record_id,
        "kind": kind,
        "bot_id": bot_id,
        "content": content[:4000],
        "confidence": confidence,
        "importance": importance,
        "source_ids": source_ids,
        "metadata": canonical_metadata,
        "authority": authority,
        "evidence": evidence,
        "effective_from": int(timestamp * 1000),
    }
