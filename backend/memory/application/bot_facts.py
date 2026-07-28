"""Canonical persistence for facts extracted from Bot output."""
from __future__ import annotations

import hashlib
import json
import time

from memory.contracts import IngestBotFactObservations, MemoryAuthorizationError
from memory.domain import ScopeKind
from memory.ports import MemoryDatabasePort, ProjectionOutboxPort

from .vector_projection import enqueue_bot_memory_projection


class BotFactObservationService:
    """Mirror legacy extraction as Bot-owned provisional canonical records."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        projection_outbox: ProjectionOutboxPort,
    ) -> None:
        self._database = database
        self._projection_outbox = projection_outbox

    async def ingest(self, command: IngestBotFactObservations) -> tuple[str, ...]:
        scope = command.scope
        if (
            scope.kind is not ScopeKind.BOT
            or scope.group_id is None
            or scope.bot_id is None
        ):
            raise MemoryAuthorizationError(
                "Bot fact observations require an isolated bot scope"
            )
        if scope.actor_id != f"bot:{scope.bot_id}":
            raise MemoryAuthorizationError(
                "Bot fact observation actor must match the owning bot"
            )

        now = int(time.time() * 1000)
        effective_from = command.observed_at
        if effective_from is None:
            effective_from = now
        conflict_ids = tuple(
            dict.fromkeys(
                item.strip()
                for item in command.legacy_conflict_ids
                if item.strip()
            )
        )
        record_ids: list[str] = []
        async with await self._database.connect(
            "memory_records", scope.group_id, write=True
        ) as db:
            for fact in command.facts:
                record_id = bot_fact_record_id(
                    scope.group_id,
                    scope.bot_id,
                    command.source_id,
                    fact.projection_id,
                )
                evidence = {
                    "source_type": "bot_reply",
                    "source_id": command.source_id,
                    "legacy_projection_id": fact.projection_id,
                    "legacy_conflict_ids": conflict_ids,
                    "extracted_by": {
                        "provider": command.provider,
                        "model": command.model,
                    },
                }
                metadata = {
                    "schema_version": "bot-fact-observation-v1",
                    "role": command.role,
                    "thread_id": command.thread_id,
                    "projection_state": "legacy_direct_write_with_durable_outbox",
                }
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO memory_records
                    (record_id,kind,group_id,bot_id,status,content,
                     task_signature,confidence,importance,source_ids,
                     metadata_json,algorithm_version,owner_type,authority,
                     subject_key,sensitivity,evidence_json,created_by,
                     effective_from,created_at,updated_at)
                    VALUES (?,'fact',?,?,'provisional',?,'',0.5,?,?,?,
                        'legacy-fact-dual-write-v1','bot','bot_observation',
                        '','group',?,?,?,?,?)""",
                    (
                        record_id,
                        scope.group_id,
                        scope.bot_id,
                        fact.content.strip()[:4000],
                        fact.importance,
                        json.dumps([command.source_id]),
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                        scope.actor_id,
                        effective_from,
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
                    "timestamp": effective_from / 1000,
                    "importance": fact.importance,
                    "mem_type": "fact",
                    "scored_by_model": f"{command.provider}/{command.model}",
                    "thread_id": command.thread_id,
                    "group_id": scope.group_id,
                }
                await enqueue_bot_memory_projection(
                    self._projection_outbox,
                    db,
                    record_id=record_id,
                    group_id=scope.group_id,
                    projection_id=fact.projection_id,
                    content=fact.content.strip()[:4000],
                    metadata=projection_metadata,
                    delete_ids=conflict_ids,
                    now_ms=now,
                )
                record_ids.append(record_id)
            await db.commit()
        return tuple(record_ids)


def bot_fact_record_id(
    group_id: int,
    bot_id: int,
    source_id: str,
    projection_id: str,
) -> str:
    raw = f"{group_id}:{bot_id}:{source_id}:{projection_id}"
    return "bot-fact:" + hashlib.sha256(raw.encode()).hexdigest()[:24]
