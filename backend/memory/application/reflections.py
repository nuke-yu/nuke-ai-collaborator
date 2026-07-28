"""Canonical persistence for Bot-synthesized consolidation reflections."""
from __future__ import annotations

import hashlib
import json
import time

from memory.contracts import IngestBotReflections, MemoryAuthorizationError
from memory.domain import ScopeKind
from memory.ports import MemoryDatabasePort


class BotReflectionService:
    """Mirror legacy reflections as Bot-owned provisional canonical records."""

    def __init__(self, database: MemoryDatabasePort) -> None:
        self._database = database

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
        record_ids: list[str] = []
        async with await self._database.connect(
            "memory_records", scope.group_id, write=True
        ) as db:
            for reflection in command.reflections:
                record_id = _record_id(
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
                    "projection_state": "legacy_chroma_direct_write",
                }
                await db.execute(
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
                        reflection.content.strip()[:4000],
                        reflection.importance,
                        json.dumps(sources),
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                        scope.actor_id,
                        reflection.observed_at,
                        now,
                        now,
                    ),
                )
                record_ids.append(record_id)
            await db.commit()
        return tuple(record_ids)


def _record_id(group_id: int, bot_id: int, projection_id: str) -> str:
    raw = f"{group_id}:{bot_id}:{projection_id}"
    return "bot-reflection:" + hashlib.sha256(raw.encode()).hexdigest()[:24]
