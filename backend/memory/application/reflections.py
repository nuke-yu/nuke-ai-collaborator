"""Canonical persistence for Bot-synthesized consolidation reflections."""
from __future__ import annotations

import hashlib
import json
import time

from memory.contracts import IngestBotReflections, MemoryAuthorizationError
from memory.domain import ScopeKind
from memory.ports import MemoryDatabasePort, ProjectionOutboxPort
from memory.infrastructure import safe_memory_mapping, safe_memory_text

from .vector_projection import enqueue_bot_memory_projection


class BotReflectionService:
    """Mirror legacy reflections as Bot-owned provisional canonical records."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        projection_outbox: ProjectionOutboxPort,
    ) -> None:
        self._database = database
        self._projection_outbox = projection_outbox

    async def ingest(self, command: IngestBotReflections) -> tuple[str, ...]:
        scope = command.scope
        if (
            scope.kind is not ScopeKind.BOT
            or scope.group_id is None
            or scope.bot_id is None
        ):
            raise MemoryAuthorizationError(
                "Bot reflections require an isolated bot scope"
            )
        if scope.actor_id != f"bot:{scope.bot_id}":
            raise MemoryAuthorizationError(
                "Bot reflection actor must match the owning bot"
            )

        now = int(time.time() * 1000)
        conflict_ids = tuple(dict.fromkeys(
            item.strip()
            for item in command.legacy_conflict_ids
            if item.strip()
        ))
        record_ids: list[str] = []
        async with await self._database.connect(
            "memory_records", scope.group_id, write=True
        ) as db:
            for reflection in command.reflections:
                record_id = bot_reflection_record_id(
                    scope.group_id,
                    scope.bot_id,
                    reflection.projection_id,
                )
                sources = tuple(dict.fromkeys(
                    source.strip()
                    for source in reflection.source_projection_ids
                    if source.strip()
                ))
                evidence = {
                    "source_type": "consolidation_reflection",
                    "legacy_projection_id": reflection.projection_id,
                    "legacy_source_projection_ids": sources,
                    "legacy_conflict_ids": conflict_ids,
                    "synthesized_by": {
                        "provider": command.provider,
                        "model": command.model,
                    },
                }
                metadata = {
                    "schema_version": "bot-reflection-v1",
                    "role": command.role,
                    "thread_id": command.thread_id,
                    "level": reflection.level,
                    "projection_state": "legacy_direct_write_with_durable_outbox",
                }
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO memory_records
                    (record_id,kind,group_id,bot_id,status,content,
                     task_signature,confidence,importance,source_ids,
                     metadata_json,algorithm_version,owner_type,authority,
                     subject_key,sensitivity,evidence_json,created_by,
                     effective_from,created_at,updated_at)
                    VALUES (?,'reflection',?,?,'provisional',?,'',0.4,?,?,?,
                        'legacy-reflection-dual-write-v1','bot','bot_inference',
                        '','group',?,?,?,?,?)""",
                    (
                        record_id,
                        scope.group_id,
                        scope.bot_id,
                        safe_memory_text(reflection.content),
                        reflection.importance,
                        json.dumps(sources),
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        safe_memory_mapping(evidence),
                        scope.actor_id,
                        reflection.observed_at,
                        now,
                        now,
                    ),
                )
                if cursor.rowcount == 0:
                    async with db.execute(
                        "SELECT status FROM memory_records WHERE record_id=?",
                        (record_id,),
                    ) as existing_cursor:
                        existing = await existing_cursor.fetchone()
                    if existing is None or existing[0] != "provisional":
                        record_ids.append(record_id)
                        continue
                projection_metadata = {
                    "bot_id": scope.bot_id,
                    "role": command.role,
                    "timestamp": reflection.observed_at / 1000,
                    "importance": reflection.importance,
                    "mem_type": "reflection",
                    "level": reflection.level,
                    "source_ids": ",".join(sources),
                    "thread_id": command.thread_id,
                    "scored_by_model": f"{command.provider}/{command.model}",
                    "group_id": scope.group_id,
                }
                await enqueue_bot_memory_projection(
                    self._projection_outbox,
                    db,
                    record_id=record_id,
                    group_id=scope.group_id,
                    projection_id=reflection.projection_id,
                    content=safe_memory_text(reflection.content),
                    metadata=projection_metadata,
                    delete_ids=conflict_ids,
                    now_ms=now,
                )
                record_ids.append(record_id)
            await db.commit()
        return tuple(record_ids)


def bot_reflection_record_id(
    group_id: int, bot_id: int, projection_id: str
) -> str:
    raw = f"{group_id}:{bot_id}:{projection_id}"
    return "bot-reflection:" + hashlib.sha256(raw.encode()).hexdigest()[:24]
