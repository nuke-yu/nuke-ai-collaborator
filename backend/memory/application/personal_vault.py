"""Canonical Personal Vault application service."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from memory.contracts import (
    CreatePersonalProjection,
    CreatePersonalRecord,
    FormatProjectedContext,
    IngestPersonalKnowledge,
    MemoryOperationError,
    ObservePersonalHabit,
)
from memory.domain import MemoryScope, ScopeKind
from memory.infrastructure import PersonalVaultDatabase, safe_memory_text
from memory.ports import PersonalKnowledgePort


_KINDS = {"profile", "expertise", "decision", "workflow", "social", "preference", "habit", "temporary"}
_SENSITIVITIES = {"private", "restricted", "secret"}


async def list_personal_apps(*, user_id: int, include_inactive: bool = True) -> list[dict[str, object]]:
    async with PersonalVaultDatabase().connect(user_id) as db:
        where = "" if include_inactive else " AND status='active'"
        async with db.execute(
            "SELECT app_id,name,status,created_at,updated_at FROM personal_apps WHERE user_id=?" + where + " ORDER BY app_id",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [{"app_id": str(r[0]), "name": str(r[1]), "status": str(r[2]), "created_at": int(r[3]), "updated_at": int(r[4])} for r in rows]


async def register_personal_app(*, user_id: int, app_id: str, name: str) -> None:
    app_id, name = app_id.strip(), name.strip()
    if not app_id or not name:
        raise ValueError("app_id and name are required")
    now = int(time.time() * 1000)
    async with PersonalVaultDatabase().connect(user_id) as db:
        await db.execute(
            """INSERT INTO personal_apps(app_id,user_id,name,status,created_at,updated_at)
               VALUES(?,?,?,'active',?,?)
               ON CONFLICT(user_id,app_id) DO UPDATE SET name=excluded.name,status='active',updated_at=excluded.updated_at""",
            (app_id, user_id, name, now, now),
        )
        await db.commit()


async def set_personal_app_status(*, user_id: int, app_id: str, active: bool) -> bool:
    async with PersonalVaultDatabase().connect(user_id) as db:
        cur = await db.execute(
            "UPDATE personal_apps SET status=?,updated_at=? WHERE user_id=? AND app_id=?",
            ("active" if active else "inactive", int(time.time() * 1000), user_id, app_id.strip()),
        )
        await db.commit()
    return cur.rowcount == 1


async def list_acl_audit_events(*, user_id: int, limit: int = 100) -> list[dict[str, object]]:
    async with PersonalVaultDatabase().connect(user_id) as db:
        async with db.execute(
            """SELECT audit_id,actor_id,scope_kind,group_id,bot_id,action,allowed,reason,created_at
               FROM personal_acl_audit_events WHERE user_id=? ORDER BY created_at DESC,audit_id DESC LIMIT ?""",
            (user_id, max(1, min(limit, 1000))),
        ) as cur:
            rows = await cur.fetchall()
    return [{"audit_id": int(r[0]), "actor_id": str(r[1]), "scope_kind": str(r[2]), "group_id": r[3], "bot_id": r[4], "action": str(r[5]), "allowed": bool(r[6]), "reason": str(r[7]), "created_at": int(r[8])} for r in rows]


class CanonicalPersonalKnowledgeService(PersonalKnowledgePort):
    def __init__(self, database: PersonalVaultDatabase | None = None) -> None:
        self._database = database or PersonalVaultDatabase()

    async def create_record(self, command: CreatePersonalRecord) -> str:
        user_id = _user_id(command.scope)
        if command.kind not in _KINDS or command.sensitivity not in _SENSITIVITIES:
            raise ValueError("unsupported personal memory kind or sensitivity")
        content = safe_memory_text(command.content)
        return await self._upsert_record(
            user_id=user_id, kind=command.kind, content=content,
            source_type=command.source_type, source_id=command.source_id,
            speaker=command.speaker, subject=str(user_id), authority="user_statement",
            sensitivity=command.sensitivity, confidence=1.0, explicit=True,
        )

    async def create_projection(self, command: CreatePersonalProjection) -> str:
        user_id = _user_id(command.scope)
        if command.scope.group_id is not None and command.scope.group_id != command.target_group_id:
            raise MemoryOperationError("projection target does not match authorized group scope")
        now = int(time.time() * 1000)
        projection_id = "projection:" + hashlib.sha256(
            f"{command.record_id}:{command.target_group_id}:{command.target_bot_id}:{command.purpose}".encode()
        ).hexdigest()[:24]
        async with self._database.connect(user_id) as db:
            if command.app_id:
                await self._require_active_app(db, user_id, command.app_id)
            async with db.execute(
                "SELECT sensitivity,status,explicit FROM personal_records WHERE record_id=? AND user_id=?",
                (command.record_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise ValueError("personal record not found")
            sensitivity, status, explicit = str(row[0]), str(row[1]), bool(row[2])
            if sensitivity == "secret":
                raise ValueError("secret personal knowledge cannot be projected")
            if sensitivity == "restricted" and not explicit:
                raise ValueError("restricted personal knowledge requires explicit confirmation")
            if status not in {"active", "provisional"}:
                raise ValueError("inactive record cannot be projected")
            await db.execute(
                """INSERT INTO personal_projections
                   (projection_id,record_id,group_id,bot_id,purpose,expires_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(projection_id) DO UPDATE SET status='active',expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
                (projection_id, command.record_id, command.target_group_id, command.target_bot_id,
                 command.purpose, command.expires_at, now, now),
            )
            await db.commit()
        return projection_id

    async def ingest(self, command: IngestPersonalKnowledge) -> str:
        user_id = _user_id(command.scope)
        authority = "user_statement" if command.asserted_by_user and str(command.subject) == str(user_id) else (
            "third_party" if str(command.subject) != str(user_id) else "observed"
        )
        return await self._upsert_record(
            user_id=user_id, kind=command.kind, content=command.statement,
            source_type=command.source_type, source_id=command.source_id,
            speaker=command.speaker, subject=command.subject or str(user_id),
            authority=authority, sensitivity=command.sensitivity,
            confidence=1.0 if authority == "user_statement" else 0.45,
            explicit=authority == "user_statement",
        )

    async def observe_habit(self, command: ObservePersonalHabit) -> str:
        record_id = await self.ingest(IngestPersonalKnowledge(
            scope=command.scope, kind="habit", statement=command.statement,
            source_type=command.source_type, source_id=command.source_id,
            context_kind=command.context_kind, observed_at=command.observed_at,
            sensitivity="private",
        ))
        user_id = _user_id(command.scope)
        async with self._database.connect(user_id) as db:
            await db.execute(
                "INSERT OR REPLACE INTO habit_evidence(record_id,source_key,context_kind,polarity,observed_at) VALUES(?,?,?,?,?)",
                (record_id, command.source_id, command.context_kind, command.polarity, command.observed_at),
            )
            await db.commit()
        return record_id

    async def format_projected_context(self, command: FormatProjectedContext) -> str:
        user_id = _user_id(command.scope)
        if command.scope.group_id is None:
            return ""
        now = int(time.time() * 1000)
        async with self._database.connect(user_id) as db:
            if command.app_id:
                await self._require_active_app(db, user_id, command.app_id)
            async with db.execute(
                """SELECT r.record_id,p.projection_id,r.kind,r.content,r.authority,r.confidence,r.status,r.explicit
                   FROM personal_projections p JOIN personal_records r ON r.record_id=p.record_id
                   WHERE r.user_id=? AND p.group_id=? AND (p.bot_id IS NULL OR p.bot_id=?) AND p.purpose=?
                     AND p.status='active' AND r.status IN ('active','provisional') AND r.sensitivity!='secret'
                     AND (p.expires_at IS NULL OR p.expires_at>?)
                   ORDER BY r.explicit DESC,r.confidence DESC LIMIT 100""",
                (user_id, command.scope.group_id, command.scope.bot_id, command.purpose, now),
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                await db.executemany(
                    """INSERT INTO personal_memory_usage_events
                       (user_id,record_id,projection_id,group_id,bot_id,session_id,purpose,used_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    [(user_id, row[0], row[1], command.scope.group_id, command.scope.bot_id,
                      command.scope.run_id or "", command.purpose, now) for row in rows],
                )
            await db.commit()
        chunks: list[str] = []
        used = 0
        for row in rows:
            line = f"- [{row[2]}/{row[4]}] {safe_memory_text(row[3], limit=2000)}"
            if used + len(line) > command.char_budget:
                break
            chunks.append(line)
            used += len(line)
        return "[Authorized personal context]\n" + "\n".join(chunks) if chunks else ""

    async def rebuild(self, scope: MemoryScope) -> Mapping[str, Any]:
        user_id = _user_id(scope)
        now = int(time.time() * 1000)
        async with self._database.connect(user_id) as db:
            cur = await db.execute("UPDATE personal_projections SET status='expired',updated_at=? WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?", (now, now))
            await db.commit()
        return {"expired_projections": cur.rowcount, "schema_version": 1}

    async def export(self, scope: MemoryScope) -> Mapping[str, Any]:
        user_id = _user_id(scope)
        async with self._database.connect(user_id) as db:
            async with db.execute("SELECT record_id,kind,content,speaker,subject,authority,sensitivity,status,source_type,source_id,confidence,explicit,valid_from,valid_to FROM personal_records WHERE user_id=? ORDER BY created_at", (user_id,)) as cur:
                records = await cur.fetchall()
            async with db.execute("SELECT projection_id,record_id,group_id,bot_id,purpose,status,expires_at FROM personal_projections ORDER BY created_at") as cur:
                projections = await cur.fetchall()
        fields = ("record_id","kind","content","speaker","subject","authority","sensitivity","status","source_type","source_id","confidence","explicit","valid_from","valid_to")
        pfields = ("projection_id","record_id","group_id","bot_id","purpose","status","expires_at")
        return {"schema_version": 1, "user_id": user_id,
                "records": [dict(zip(fields, row)) for row in records],
                "projections": [dict(zip(pfields, row)) for row in projections]}

    async def get_record_impact(self, scope: MemoryScope, record_id: str) -> Mapping[str, Any]:
        user_id = _user_id(scope)
        async with self._database.connect(user_id) as db:
            async with db.execute("SELECT projection_id,group_id,bot_id,purpose,status,expires_at FROM personal_projections WHERE record_id=? ORDER BY created_at DESC", (record_id,)) as cur:
                rows = await cur.fetchall()
        projections = [{"projection_id": r[0], "group_id": r[1], "bot_id": r[2], "purpose": r[3], "status": r[4], "expires_at": r[5]} for r in rows]
        return {"record_id": record_id, "active_projections": [p for p in projections if p["status"] == "active"], "projections": projections, "usage_events": [], "affected_group_ids": sorted({p["group_id"] for p in projections}), "affected_session_ids": []}

    async def delete(self, scope: MemoryScope) -> bool:
        user_id = _user_id(scope)
        async with self._database.connect(user_id) as db:
            cur = await db.execute("DELETE FROM personal_records WHERE user_id=?", (user_id,))
            await db.commit()
        return cur.rowcount > 0

    async def delete_record(self, scope: MemoryScope, record_id: str) -> bool:
        user_id = _user_id(scope)
        async with self._database.connect(user_id) as db:
            cur = await db.execute("DELETE FROM personal_records WHERE user_id=? AND record_id=?", (user_id, record_id))
            await db.commit()
        return cur.rowcount > 0

    async def revoke_projection(self, scope: MemoryScope, projection_id: str) -> bool:
        user_id = _user_id(scope)
        async with self._database.connect(user_id) as db:
            cur = await db.execute("DELETE FROM personal_projections WHERE projection_id=? AND record_id IN (SELECT record_id FROM personal_records WHERE user_id=?)", (projection_id, user_id))
            await db.commit()
        return cur.rowcount > 0

    async def _upsert_record(self, *, user_id: int, kind: str, content: str, source_type: str, source_id: str, speaker: str, subject: str, authority: str, sensitivity: str, confidence: float, explicit: bool) -> str:
        if kind not in _KINDS or sensitivity not in _SENSITIVITIES:
            raise ValueError("unsupported personal memory kind or sensitivity")
        safe = safe_memory_text(content)
        now = int(time.time() * 1000)
        record_id = "personal:" + hashlib.sha256(f"{user_id}:{source_type}:{source_id}:{kind}:{safe}".encode()).hexdigest()[:24]
        async with self._database.connect(user_id) as db:
            await db.execute(
                """INSERT INTO personal_records(record_id,user_id,kind,content,speaker,subject,authority,sensitivity,status,source_type,source_id,confidence,explicit,valid_from,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(record_id) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at,
                     confidence=MAX(personal_records.confidence,excluded.confidence),explicit=MAX(personal_records.explicit,excluded.explicit)""",
                (record_id,user_id,kind,safe,speaker,subject,authority,sensitivity,"active" if explicit else "provisional",source_type,source_id,max(0,min(1,confidence)),int(explicit),now,now,now),
            )
            await db.commit()
        return record_id

    @staticmethod
    async def _require_active_app(db, user_id: int, app_id: str) -> None:
        async with db.execute("SELECT status FROM personal_apps WHERE user_id=? AND app_id=?", (user_id, app_id.strip())) as cur:
            row = await cur.fetchone()
        if not row or str(row[0]) != "active":
            raise ValueError("personal app is inactive or not registered")


def _user_id(scope: MemoryScope) -> int:
    if scope.kind is not ScopeKind.PERSONAL or scope.user_id is None:
        raise MemoryOperationError("personal knowledge operation requires personal scope")
    return scope.user_id
