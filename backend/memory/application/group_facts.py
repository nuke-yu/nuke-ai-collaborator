"""Canonical Group Fact write use case."""
from __future__ import annotations

import hashlib
import json
import re
import time

from memory.contracts import IngestGroupFact, MemoryAuthorizationError
from memory.domain import (
    FactAuthority,
    FactSensitivity,
    MemoryOwnerType,
    ScopeKind,
    admit_group_fact,
)
from memory.ports import MemoryDatabasePort

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
                    command.statement.strip()[:4000],
                    _CONFIDENCE[admission.authority],
                    0.8 if admission.can_activate else 0.4,
                    json.dumps([command.source_id]),
                    json.dumps(metadata, ensure_ascii=False),
                    MemoryOwnerType.GROUP.value,
                    admission.authority.value,
                    subject_key,
                    sensitivity.value,
                    json.dumps(evidence, ensure_ascii=False),
                    scope.actor_id,
                    now,
                    now,
                    now,
                ),
            )
            await db.commit()
        return record_id

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
