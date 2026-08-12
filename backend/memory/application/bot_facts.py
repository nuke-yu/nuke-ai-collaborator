"""Canonical persistence for facts extracted from Bot output."""
from __future__ import annotations

import hashlib
import json
import time

from memory.contracts import IngestBotFactObservations, MemoryAuthorizationError
from memory.domain import MemoryRelationType, ScopeKind
from memory.ports import MemoryDatabasePort, ProjectionOutboxPort
from memory.infrastructure import safe_memory_mapping, safe_memory_text

from .relations import memory_relation_id
from .vector_projection import (
    enqueue_bot_memory_projection,
    enqueue_bot_memory_projection_delete,
)


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
        conflict_replacements = {
            old_id.strip(): replacement_id.strip()
            for old_id, replacement_id in command.legacy_conflict_replacements
            if old_id.strip() in conflict_ids and replacement_id.strip()
        }
        record_ids: list[str] = []
        replacement_records: dict[str, str] = {}
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
                    "mem0_action": fact.algorithm_action,
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
                        safe_memory_text(fact.content),
                        fact.importance,
                        json.dumps([command.source_id]),
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        safe_memory_mapping(evidence),
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
                replacement_records[fact.projection_id] = record_id
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
                    content=safe_memory_text(fact.content),
                    metadata=projection_metadata,
                    delete_ids=conflict_ids,
                    now_ms=now,
                )
                record_ids.append(record_id)
            await self._supersede_conflicting_facts(
                db,
                group_id=scope.group_id,
                bot_id=scope.bot_id,
                actor_id=scope.actor_id,
                source_id=command.source_id,
                conflict_ids=conflict_ids,
                conflict_replacements=conflict_replacements,
                replacement_records=replacement_records,
                now=now,
            )
            await db.commit()
        return tuple(record_ids)

    async def _supersede_conflicting_facts(
        self,
        db,
        *,
        group_id: int,
        bot_id: int,
        actor_id: str,
        source_id: str,
        conflict_ids: tuple[str, ...],
        conflict_replacements: dict[str, str],
        replacement_records: dict[str, str],
        now: int,
    ) -> None:
        if not conflict_ids:
            return
        async with db.execute(
            """SELECT record_id,evidence_json FROM memory_records
            WHERE group_id=? AND bot_id=? AND kind='fact'
              AND owner_type='bot' AND status='provisional'""",
            (group_id, bot_id),
        ) as cursor:
            rows = await cursor.fetchall()

        conflicts = set(conflict_ids)
        for old_record_id, raw_evidence in rows:
            try:
                old_evidence = json.loads(raw_evidence or "{}")
                old_projection_id = str(
                    old_evidence.get("legacy_projection_id") or ""
                ).strip()
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if old_projection_id not in conflicts:
                continue

            replacement_projection_id = conflict_replacements.get(
                old_projection_id, ""
            )
            replacement_record_id = replacement_records.get(
                replacement_projection_id
            )
            if replacement_record_id == old_record_id:
                continue
            cursor = await db.execute(
                """UPDATE memory_records
                SET status='superseded',valid_to=?,superseded_by=?,updated_at=?
                WHERE record_id=? AND group_id=? AND bot_id=?
                  AND kind='fact' AND status='provisional'""",
                (
                    now,
                    replacement_record_id,
                    now,
                    old_record_id,
                    group_id,
                    bot_id,
                ),
            )
            if cursor.rowcount != 1:
                continue

            await enqueue_bot_memory_projection_delete(
                self._projection_outbox,
                db,
                record_id=str(old_record_id),
                group_id=group_id,
                projection_id=old_projection_id,
                now_ms=now,
            )
            if replacement_record_id is not None:
                await _insert_supersedes_relation(
                    db,
                    group_id=group_id,
                    from_record_id=replacement_record_id,
                    to_record_id=str(old_record_id),
                    source_id=source_id,
                    old_projection_id=old_projection_id,
                    replacement_projection_id=replacement_projection_id,
                    actor_id=actor_id,
                    now=now,
                )


def bot_fact_record_id(
    group_id: int,
    bot_id: int,
    source_id: str,
    projection_id: str,
) -> str:
    raw = f"{group_id}:{bot_id}:{source_id}:{projection_id}"
    return "bot-fact:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


async def _insert_supersedes_relation(
    db,
    *,
    group_id: int,
    from_record_id: str,
    to_record_id: str,
    source_id: str,
    old_projection_id: str,
    replacement_projection_id: str,
    actor_id: str,
    now: int,
) -> None:
    relation_source_id = f"{source_id}:{old_projection_id}"
    relation_id = memory_relation_id(
        group_id,
        from_record_id,
        to_record_id,
        MemoryRelationType.SUPERSEDES,
        "fact_conflict",
        relation_source_id,
    )
    evidence = {
        "resolution": "legacy_batch_conflict",
        "old_projection_id": old_projection_id,
        "replacement_projection_id": replacement_projection_id,
    }
    await db.execute(
        """INSERT OR IGNORE INTO memory_relations
        (relation_id,group_id,from_record_id,to_record_id,relation_type,status,
         source_type,source_id,evidence_json,created_by,effective_from,created_at)
        VALUES (?,?,?,?,?,'active','fact_conflict',?,?,?,?,?)""",
        (
            relation_id,
            group_id,
            from_record_id,
            to_record_id,
            "supersedes",
            relation_source_id,
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            actor_id,
            now,
            now,
        ),
    )
