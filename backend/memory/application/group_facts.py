"""Canonical Group Fact write use case."""
from __future__ import annotations

import hashlib
import json
import re
import time

from memory.contracts import (
    IngestGroupFact,
    MemoryAuthorizationError,
    MemoryHit,
    RecallGroupFacts,
    RecallResult,
)
from memory.domain import (
    FactAuthority,
    FactSensitivity,
    MemoryOwnerType,
    ScopeKind,
    admit_group_fact,
)
from memory.ports import MemoryDatabasePort
from memory.domain.safety import safe_memory_mapping, safe_memory_text

_CONFIDENCE = {
    FactAuthority.USER_EXPLICIT: 0.95,
    FactAuthority.PROJECT_AUTHORITATIVE: 0.9,
    FactAuthority.SYSTEM_DETERMINISTIC: 1.0,
    FactAuthority.BOT_OBSERVATION: 0.5,
    FactAuthority.BOT_INFERENCE: 0.3,
}


class GroupFactService:
    def __init__(self, database: MemoryDatabasePort) -> None:
        self._database = database

    async def ingest_fact(self, command: IngestGroupFact) -> str:
        scope = command.scope
        if scope.kind not in {ScopeKind.GROUP, ScopeKind.BOT} or scope.group_id is None:
            raise MemoryAuthorizationError("Group Fact requires group or bot scope")
        sensitivity = FactSensitivity(command.sensitivity)
        admission = admit_group_fact(command.source_type, sensitivity)
        self._authorize_source(scope.actor_id, admission.authority)
        subject_key = _normalize_subject(command.subject_key)
        record_id = _record_id(
            scope.group_id,
            command.source_type,
            command.source_id,
            subject_key,
        )
        now = int(time.time() * 1000)
        evidence = {
            "source_type": command.source_type,
            "source_id": command.source_id,
            "details": dict(command.evidence),
        }
        metadata = {
            "schema_version": "group-fact-v1",
            "authority": admission.authority.value,
        }
        async with await self._database.connect(
            "memory_records", scope.group_id, write=True
        ) as db:
            if admission.status == "active":
                await db.execute(
                    """UPDATE memory_records
                    SET status='superseded',valid_to=?,superseded_by=?,
                        updated_at=?
                    WHERE group_id=? AND owner_type='group'
                      AND kind='group_fact' AND subject_key=?
                      AND status='active' AND record_id!=?""",
                    (
                        now,
                        record_id,
                        now,
                        scope.group_id,
                        subject_key,
                        record_id,
                    ),
                )
            await db.execute(
                """INSERT INTO memory_records
                (record_id,kind,group_id,bot_id,status,content,
                 confidence,importance,source_ids,metadata_json,
                 algorithm_version,owner_type,authority,subject_key,
                 sensitivity,evidence_json,created_by,effective_from,
                 created_at,updated_at)
                VALUES (?,'group_fact',?,NULL,?,?,?,?,?,?,
                    'group-fact-v1',?,?,?,?,?,?,?,?,?)
                ON CONFLICT(record_id) DO UPDATE SET
                    status=excluded.status,content=excluded.content,
                    confidence=excluded.confidence,
                    importance=excluded.importance,
                    source_ids=excluded.source_ids,
                    metadata_json=excluded.metadata_json,
                    authority=excluded.authority,
                    sensitivity=excluded.sensitivity,
                    evidence_json=excluded.evidence_json,
                    created_by=excluded.created_by,
                    effective_from=excluded.effective_from,
                    valid_to=NULL,superseded_by=NULL,
                    updated_at=excluded.updated_at""",
                (
                    record_id,
                    scope.group_id,
                    admission.status,
                    safe_memory_text(command.statement),
                    _CONFIDENCE[admission.authority],
                    0.8 if admission.can_activate else 0.4,
                    json.dumps([command.source_id]),
                    json.dumps(metadata, ensure_ascii=False),
                    MemoryOwnerType.GROUP.value,
                    admission.authority.value,
                    subject_key,
                    sensitivity.value,
                    safe_memory_mapping(evidence),
                    scope.actor_id,
                    now,
                    now,
                    now,
                ),
            )
            await db.commit()
        return record_id

    async def recall_facts(self, query: RecallGroupFacts) -> RecallResult:
        scope = query.scope
        if scope.kind not in {ScopeKind.GROUP, ScopeKind.BOT} or scope.group_id is None:
            raise MemoryAuthorizationError("Group Fact recall requires group scope")
        terms = _terms(query.query)
        fts_query = _build_fts_query(query.query)
        rows: list[tuple[Any, ...]] = []
        candidate_source = "fts"

        async with await self._database.connect(
            "memory_records", scope.group_id, write=False
        ) as db:
            if fts_query:
                try:
                    async with db.execute(
                        """SELECT r.record_id, r.content, r.confidence, r.authority,
                            r.subject_key, r.source_ids, r.updated_at, fts.rank
                        FROM memory_records_fts fts
                        JOIN memory_records r ON fts.record_id = r.record_id
                        WHERE fts MATCH ? AND r.group_id=? AND r.owner_type='group'
                          AND r.kind='group_fact' AND r.status='active'
                          AND r.sensitivity IN ('public','group')
                        ORDER BY fts.rank LIMIT 500""",
                        (fts_query, scope.group_id),
                    ) as cursor:
                        rows = await cursor.fetchall()
                except Exception:
                    rows = []

            # Fallback scan across active group facts if FTS returned no rows or errored
            if not rows:
                candidate_source = "fallback_scan"
                async with db.execute(
                    """SELECT record_id, content, confidence, authority,
                        subject_key, source_ids, updated_at, 0.0 as rank
                    FROM memory_records
                    WHERE group_id=? AND owner_type='group'
                      AND kind='group_fact' AND status='active'
                      AND sensitivity IN ('public','group')
                    ORDER BY updated_at DESC LIMIT 1000""",
                    (scope.group_id,),
                ) as cursor:
                    rows = await cursor.fetchall()

        ranked = []
        now_ms = time.time() * 1000
        norm_query_subject = _normalize_subject_safe(query.query)

        for row in rows:
            candidate_terms = _terms(f"{row[4]} {row[1]}")
            lexical = (
                len(terms & candidate_terms) / max(1, len(terms | candidate_terms))
                if terms
                else 0.1
            )
            subject_boost = (
                0.3
                if norm_query_subject and norm_query_subject in str(row[4]).lower()
                else 0.0
            )
            age_days = max(0.0, (now_ms - float(row[6])) / (86400 * 1000))
            recency = max(0.0, 0.1 * (1.0 - (age_days / 30.0)))
            score = 0.5 * lexical + subject_boost + 0.15 * float(row[2]) + recency
            if lexical > 0 or subject_boost > 0 or candidate_source == "fallback_scan":
                ranked.append((score, row))

        ranked.sort(key=lambda item: (item[0], item[1][6]), reverse=True)

        hits = []
        rendered = []
        used = 0
        for score, row in ranked[: query.limit]:
            safe_content = str(row[1]).replace(
                "</untrusted_group_fact>", ""
            )[:1000]
            block = (
                f'<untrusted_group_fact subject="{row[4]}" '
                f'authority="{row[3]}">\n'
                f"{safe_content}\n</untrusted_group_fact>"
            )
            if used + len(block) > query.char_budget:
                break
            used += len(block)
            rendered.append(block)
            hits.append(
                MemoryHit(
                    record_id=row[0],
                    kind="group_fact",
                    content=safe_content,
                    score=score,
                    provenance={
                        "group_id": scope.group_id,
                        "authority": row[3],
                        "subject_key": row[4],
                        "source_ids": tuple(json.loads(row[5] or "[]")),
                    },
                )
            )
        context = (
            "[Canonical Group Facts]\n" + "\n".join(rendered)
            if rendered
            else ""
        )
        return RecallResult(
            hits=tuple(hits),
            rendered_context=context,
            algorithm_trace=(
                {
                    "algorithm_id": f"nuke.group_fact.{candidate_source}",
                    "version": "v2_fts5",
                    "candidate_count": len(rows),
                },
            ),
        )

    @staticmethod
    def _authorize_source(actor_id: str, authority: FactAuthority) -> None:
        if authority is FactAuthority.USER_EXPLICIT:
            allowed = actor_id.startswith("user:")
        elif authority in {
            FactAuthority.PROJECT_AUTHORITATIVE,
            FactAuthority.SYSTEM_DETERMINISTIC,
        }:
            allowed = actor_id.startswith(("user:", "system:"))
            if authority is FactAuthority.SYSTEM_DETERMINISTIC:
                allowed = actor_id.startswith("system:")
        else:
            allowed = actor_id.startswith("bot:")
        if not allowed:
            raise MemoryAuthorizationError(
                f"actor {actor_id!r} cannot assert {authority.value}"
            )


def _normalize_subject(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.:/-]+", "-", value.strip().lower())
    normalized = normalized.strip("-")[:200]
    if not normalized:
        raise ValueError("subject_key must contain a stable identifier")
    return normalized


def _record_id(
    group_id: int, source_type: str, source_id: str, subject_key: str
) -> str:
    raw = f"{group_id}:{source_type}:{source_id}:{subject_key}"
    return "group-fact:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _terms(value: str) -> set[str]:
    lowered = value.lower()
    terms = set(re.findall(r"[a-z0-9_.:/-]{2,}", lowered))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    terms.update(
        chinese[index : index + 2]
        for index in range(max(0, len(chinese) - 1))
    )
    return terms


def _build_fts_query(raw_query: str) -> str:
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", raw_query.strip())
    tokens = [t for t in cleaned.split() if len(t) > 0][:10]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"*' if t.isascii() and t.isalnum() else f'"{t}"' for t in tokens)


def _normalize_subject_safe(value: str) -> str:
    return re.sub(r"[^a-z0-9_.:/-]+", "-", value.strip().lower()).strip("-")
