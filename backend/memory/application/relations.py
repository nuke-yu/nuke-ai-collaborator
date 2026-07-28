"""Group-isolated storage for canonical Memory relations."""
from __future__ import annotations

import hashlib
import json
import time

from memory.contracts import (
    CreateMemoryRelation,
    MemoryAuthorizationError,
    MemoryOperationError,
    MemoryRelation,
    RecallMemoryRelations,
)
from memory.domain import MemoryRelationType, ScopeKind
from memory.ports import MemoryDatabasePort


class CanonicalRelationService:
    """Persist auditable links without turning relations into a retrieval graph."""

    def __init__(self, database: MemoryDatabasePort) -> None:
        self._database = database

    async def create(self, command: CreateMemoryRelation) -> str:
        group_id = _require_group_scope(command.scope.kind, command.scope.group_id)
        relation_type = MemoryRelationType(command.relation_type)
        from_record_id = command.from_record_id.strip()
        to_record_id = command.to_record_id.strip()
        source_type = command.source_type.strip()
        source_id = command.source_id.strip()
        relation_id = memory_relation_id(
            group_id,
            from_record_id,
            to_record_id,
            relation_type,
            source_type,
            source_id,
        )
        now = int(time.time() * 1000)
        effective_from = command.effective_from
        if effective_from is None:
            effective_from = now
        try:
            evidence_json = json.dumps(
                dict(command.evidence), ensure_ascii=False, sort_keys=True
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("relation evidence must be JSON serializable") from exc

        async with await self._database.connect(
            "memory_relations", group_id, write=True
        ) as db:
            async with db.execute(
                """SELECT record_id FROM memory_records
                WHERE group_id=? AND record_id IN (?,?)""",
                (group_id, from_record_id, to_record_id),
            ) as cursor:
                endpoints = {str(row[0]) for row in await cursor.fetchall()}
            if endpoints != {from_record_id, to_record_id}:
                raise MemoryOperationError(
                    "memory relation endpoints were not found in the requested group"
                )
            await db.execute(
                """INSERT OR IGNORE INTO memory_relations
                (relation_id,group_id,from_record_id,to_record_id,
                 relation_type,status,source_type,source_id,evidence_json,
                 created_by,effective_from,created_at)
                VALUES (?,?,?,?,?,'active',?,?,?,?,?,?)""",
                (
                    relation_id,
                    group_id,
                    from_record_id,
                    to_record_id,
                    relation_type.value,
                    source_type,
                    source_id,
                    evidence_json,
                    command.scope.actor_id,
                    effective_from,
                    now,
                ),
            )
            await db.commit()
        return relation_id

    async def recall(
        self, query: RecallMemoryRelations
    ) -> tuple[MemoryRelation, ...]:
        group_id = _require_group_scope(query.scope.kind, query.scope.group_id)
        relation_types = tuple(
            MemoryRelationType(value).value for value in query.relation_types
        )
        params: list[object] = [group_id, query.record_id, query.record_id]
        type_filter = ""
        if relation_types:
            placeholders = ",".join("?" for _ in relation_types)
            type_filter = f" AND relation_type IN ({placeholders})"
            params.extend(relation_types)
        async with await self._database.connect(
            "memory_relations", group_id, write=False
        ) as db:
            async with db.execute(
                """SELECT relation_id,group_id,from_record_id,to_record_id,
                    relation_type,source_type,source_id,evidence_json,
                    created_by,effective_from,valid_to,status
                FROM memory_relations
                WHERE group_id=? AND status='active'
                  AND (from_record_id=? OR to_record_id=?)"""
                + type_filter
                + " ORDER BY effective_from,relation_id",
                tuple(params),
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(
            MemoryRelation(
                relation_id=str(row[0]),
                group_id=int(row[1]),
                from_record_id=str(row[2]),
                to_record_id=str(row[3]),
                relation_type=MemoryRelationType(row[4]),
                source_type=str(row[5]),
                source_id=str(row[6]),
                evidence=json.loads(row[7] or "{}"),
                created_by=str(row[8]),
                effective_from=int(row[9]),
                valid_to=None if row[10] is None else int(row[10]),
                status=str(row[11]),
            )
            for row in rows
        )


def _require_group_scope(kind: ScopeKind, group_id: int | None) -> int:
    if kind not in {ScopeKind.GROUP, ScopeKind.BOT} or group_id is None:
        raise MemoryAuthorizationError(
            "canonical Memory relations require group or bot scope"
        )
    return group_id


def memory_relation_id(
    group_id: int,
    from_record_id: str,
    to_record_id: str,
    relation_type: MemoryRelationType,
    source_type: str,
    source_id: str,
) -> str:
    raw = (
        f"{group_id}:{from_record_id}:{relation_type.value}:{to_record_id}:"
        f"{source_type}:{source_id}"
    )
    return "memory-relation:" + hashlib.sha256(raw.encode()).hexdigest()[:24]
