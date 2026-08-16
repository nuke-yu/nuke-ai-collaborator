"""Group-isolated storage for canonical Memory relations."""
from __future__ import annotations

import hashlib
import json
import time
import re
from typing import Any, Awaitable, Callable, Mapping

from memory.contracts import (
    CreateMemoryRelation,
    MemoryAuthorizationError,
    MemoryOperationError,
    MemoryRelation,
    RecallMemoryRelations,
)
from memory.domain import MemoryRelationType, ScopeKind
from memory.ports import MemoryDatabasePort
from memory.domain.safety import safe_memory_mapping, safe_memory_text


class CanonicalRelationService:
    """Persist auditable links without turning relations into a retrieval graph."""

    def __init__(self, database: MemoryDatabasePort,
                 authorizer: Callable[[Any], Awaitable[bool]] | None = None) -> None:
        self._database = database
        self._authorizer = authorizer

    async def create(self, command: CreateMemoryRelation) -> str:
        group_id = _require_group_scope(command.scope.kind, command.scope.group_id)
        if self._authorizer is not None and not await self._authorizer(command.scope):
            raise MemoryAuthorizationError("actor is not authorized to write memory relations")
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
            evidence_json = safe_memory_mapping(command.evidence)
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

    async def create_from_candidates(
        self,
        *,
        scope,
        text: str,
        source_id: str,
        ai_call_fn: Callable[..., Awaitable[Any]],
    ) -> tuple[str, ...]:
        """Extract relation candidates, then persist only validated endpoints.

        The LLM response is bounded JSON evidence. Every candidate must point
        at existing record IDs and a known ``MemoryRelationType`` before the
        normal authorization/transaction path is used.
        """
        prompt = (
            "Extract relation candidates as JSON array with keys "
            "from_record_id,to_record_id,relation_type,evidence. "
            "Never invent record IDs. Text: " + str(text or "")[:4000]
        )
        try:
            response = await ai_call_fn(
                "You are a conservative memory relation extractor.",
                [{"role": "user", "content": prompt}],
            )
            raw = response.get("content", "") if isinstance(response, dict) else str(response)
            match = re.search(r"\[[^\]]*\]", raw, re.DOTALL)
            candidates = json.loads(match.group(0)) if match else []
        except Exception:
            return ()
        if not isinstance(candidates, list):
            return ()
        created: list[str] = []
        for candidate in candidates[:32]:
            if not isinstance(candidate, Mapping):
                continue
            try:
                relation_type = MemoryRelationType(str(candidate["relation_type"]))
                evidence = candidate.get("evidence")
                if not isinstance(evidence, Mapping):
                    evidence = {}
                relation_id = await self.create(
                    CreateMemoryRelation(
                        scope=scope,
                        from_record_id=str(candidate["from_record_id"]),
                        to_record_id=str(candidate["to_record_id"]),
                        relation_type=relation_type,
                        source_type="llm_relation_candidate",
                        source_id=source_id,
                        evidence={
                            **dict(evidence),
                            "text_excerpt": safe_memory_text(text, limit=500),
                        },
                    )
                )
                created.append(relation_id)
            except (KeyError, TypeError, ValueError, MemoryOperationError):
                continue
        return tuple(created)

    async def recall(
        self, query: RecallMemoryRelations
    ) -> tuple[MemoryRelation, ...]:
        group_id = _require_group_scope(query.scope.kind, query.scope.group_id)
        relation_types = tuple(
            MemoryRelationType(value).value for value in query.relation_types
        )
        type_filter = ""
        type_params: list[object] = []
        if relation_types:
            placeholders = ",".join("?" for _ in relation_types)
            type_filter = f" AND relation_type IN ({placeholders})"
            type_params.extend(relation_types)
        temporal_filter = ""
        temporal_params: list[object] = []
        if query.as_of is not None:
            temporal_filter = (
                " AND effective_from<=?"
                " AND (valid_to IS NULL OR valid_to>?)"
            )
            temporal_params.extend((query.as_of, query.as_of))

        # Bounded breadth-first traversal.  Each hop is queried independently
        # so the SQL remains parameter-safe and a group can never leak into a
        # neighboring graph.  Relation IDs deduplicate cycles.
        frontier = {query.record_id}
        seen_nodes = {query.record_id}
        seen_relations: set[str] = set()
        rows = []
        async with await self._database.connect(
            "memory_relations", group_id, write=False
        ) as db:
            for _ in range(query.max_hops):
                if not frontier:
                    break
                hop_rows = []
                # SQLite defaults to 999 bind variables. Keep each frontier
                # batch bounded (including temporal/type filters) while still
                # traversing the complete high-fanout graph.
                frontier_values = sorted(frontier)
                for offset in range(0, len(frontier_values), 50):
                    batch = frontier_values[offset:offset + 50]
                    placeholders = ",".join("?" for _ in batch)
                    params: list[object] = [group_id, *batch, *batch]
                    params.extend(temporal_params)
                    params.extend(type_params)
                    source_table = (
                        "(SELECT relation_id,group_id,from_record_id,to_record_id,relation_type,"
                        "source_type,source_id,evidence_json,created_by,effective_from,valid_to,status "
                        "FROM memory_relations UNION ALL SELECT relation_id,group_id,from_record_id,"
                        "to_record_id,relation_type,source_type,source_id,evidence_json,created_by,"
                        "effective_from,valid_to,status FROM memory_relations_archive)"
                    )
                    status_filter = "status='active'"
                    if query.as_of is not None:
                        status_filter = "status IN ('active','archived')"
                    async with db.execute(
                        """SELECT relation_id,group_id,from_record_id,to_record_id,
                            relation_type,source_type,source_id,evidence_json,
                            created_by,effective_from,valid_to,status
                        FROM """ + source_table + """
                        WHERE group_id=? AND """ + status_filter + """
                          AND (from_record_id IN ("""
                        + placeholders
                        + ") OR to_record_id IN ("
                        + placeholders
                        + "))"
                        + temporal_filter
                        + type_filter
                        + " ORDER BY effective_from,relation_id",
                        tuple(params),
                    ) as cursor:
                        hop_rows.extend(await cursor.fetchall())
                next_frontier: set[str] = set()
                for row in hop_rows:
                    relation_id = str(row[0])
                    if relation_id in seen_relations:
                        continue
                    seen_relations.add(relation_id)
                    rows.append(row)
                    left, right = str(row[2]), str(row[3])
                    neighbor = right if left in frontier else left
                    if neighbor not in seen_nodes:
                        seen_nodes.add(neighbor)
                        next_frontier.add(neighbor)
                frontier = next_frontier
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

    async def archive_before(self, scope, before: int, limit: int = 1000) -> int:
        """Move invalidated historical edges to a cold table atomically."""
        group_id = _require_group_scope(scope.kind, scope.group_id)
        if before < 0 or limit < 1:
            raise ValueError("before must be non-negative and limit must be positive")
        if self._authorizer is not None and not await self._authorizer(scope):
            raise MemoryAuthorizationError("actor is not authorized to archive memory relations")
        async with await self._database.connect("memory_relations", group_id, write=True) as db:
            await db.execute(
                """INSERT OR IGNORE INTO memory_relations_archive
                   (relation_id,group_id,from_record_id,to_record_id,relation_type,status,
                    source_type,source_id,evidence_json,created_by,effective_from,valid_to,
                    created_at,archived_at)
                   SELECT relation_id,group_id,from_record_id,to_record_id,relation_type,'archived',
                          source_type,source_id,evidence_json,created_by,effective_from,valid_to,
                          created_at,CAST(strftime('%s','now') AS INTEGER)*1000
                   FROM memory_relations
                   WHERE group_id=? AND valid_to IS NOT NULL AND valid_to<=? AND status='active'
                   ORDER BY valid_to,relation_id LIMIT ?""",
                (group_id, before, limit),
            )
            cursor = await db.execute(
                """DELETE FROM memory_relations
                   WHERE group_id=? AND valid_to IS NOT NULL AND valid_to<=? AND status='active'
                     AND relation_id IN (SELECT relation_id FROM memory_relations_archive WHERE group_id=?)""",
                (group_id, before, group_id),
            )
            await db.commit()
            return int(cursor.rowcount if cursor.rowcount is not None else 0)


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
