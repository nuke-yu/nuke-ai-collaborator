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
from memory.contracts.versions import PERSONAL_SCHEMA_VERSION
from memory.domain import MemoryScope, ScopeKind
from memory.domain.safety import safe_memory_text
from memory.ports import PersonalKnowledgePort, PersonalVaultDatabasePort


_KINDS = {"profile", "expertise", "decision", "workflow", "social", "preference", "habit", "temporary"}
_SENSITIVITIES = {"private", "restricted", "secret"}
_EXPORT_LIMIT = 1_000


async def list_personal_apps(*, database: PersonalVaultDatabasePort, user_id: int, include_inactive: bool = True) -> list[dict[str, object]]:
    async with database.connect(user_id) as db:
        where = "" if include_inactive else " AND status='active'"
        async with db.execute(
            "SELECT app_id,name,status,created_at,updated_at FROM personal_apps WHERE user_id=?" + where + " ORDER BY app_id LIMIT ?",
            (user_id, _EXPORT_LIMIT),
        ) as cur:
            rows = await cur.fetchall()
    return [{"app_id": safe_memory_text(r[0], limit=200), "name": safe_memory_text(r[1], limit=500), "status": safe_memory_text(r[2], limit=40), "created_at": int(r[3]), "updated_at": int(r[4])} for r in rows]


async def register_personal_app(*, database: PersonalVaultDatabasePort, user_id: int, app_id: str, name: str) -> None:
    app_id, name = _vault_text(app_id, 200), _vault_text(name, 500)
    if not app_id or not name:
        raise ValueError("app_id and name are required")
    now = int(time.time() * 1000)
    async with database.connect(user_id) as db:
        await db.execute(
            """INSERT INTO personal_apps(app_id,user_id,name,status,created_at,updated_at)
               VALUES(?,?,?,'active',?,?)
               ON CONFLICT(user_id,app_id) DO UPDATE SET name=excluded.name,status='active',updated_at=excluded.updated_at""",
            (app_id, user_id, name, now, now),
        )
        await db.commit()


async def set_personal_app_status(*, database: PersonalVaultDatabasePort, user_id: int, app_id: str, active: bool) -> bool:
    app_id = _vault_text(app_id, 200)
    async with database.connect(user_id) as db:
        cur = await db.execute(
            "UPDATE personal_apps SET status=?,updated_at=? WHERE user_id=? AND app_id=?",
            ("active" if active else "inactive", int(time.time() * 1000), user_id, app_id.strip()),
        )
        await db.commit()
    return cur.rowcount == 1


async def list_acl_audit_events(*, database: PersonalVaultDatabasePort, user_id: int, limit: int = 100) -> list[dict[str, object]]:
    async with database.connect(user_id) as db:
        async with db.execute(
            """SELECT audit_id,actor_id,scope_kind,group_id,bot_id,action,allowed,reason,created_at
               FROM personal_acl_audit_events WHERE user_id=? ORDER BY created_at DESC,audit_id DESC LIMIT ?""",
            (user_id, max(1, min(limit, 1000))),
        ) as cur:
            rows = await cur.fetchall()
    return [{"audit_id": int(r[0]), "actor_id": safe_memory_text(r[1], limit=200), "scope_kind": safe_memory_text(r[2], limit=80), "group_id": r[3], "bot_id": r[4], "action": safe_memory_text(r[5], limit=100), "allowed": bool(r[6]), "reason": safe_memory_text(r[7], limit=1000), "created_at": int(r[8])} for r in rows]


async def set_personal_access_rule(*, database: PersonalVaultDatabasePort, user_id: int, subject_type: str, subject_id: str,
                                   object_type: str, object_id: str, action: str,
                                   effect: str) -> None:
    if effect not in {"allow", "deny"}:
        raise ValueError("effect must be allow or deny")
    values = _normalize_access_rule_key(subject_type, subject_id, object_type, object_id, action)
    async with database.connect(user_id) as db:
        await db.execute(
            """INSERT INTO personal_access_control_actions
               (user_id,subject_type,subject_id,object_type,object_id,action,effect,created_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,subject_type,subject_id,object_type,object_id,action)
               DO UPDATE SET effect=excluded.effect,created_at=excluded.created_at""",
            (user_id, *values, effect, int(time.time() * 1000)),
        )
        await db.commit()


async def delete_personal_access_rule(*, database: PersonalVaultDatabasePort, user_id: int, subject_type: str, subject_id: str,
                                      object_type: str, object_id: str, action: str) -> bool:
    values = _normalize_access_rule_key(subject_type, subject_id, object_type, object_id, action)
    async with database.connect(user_id) as db:
        cur = await db.execute(
            """DELETE FROM personal_access_control_actions
               WHERE user_id=? AND subject_type=? AND subject_id=? AND object_type=?
                 AND object_id=? AND action=?""",
            (user_id, *values),
        )
        await db.commit()
    return cur.rowcount == 1


class CanonicalPersonalKnowledgeService(PersonalKnowledgePort):
    def __init__(self, database: PersonalVaultDatabasePort) -> None:
        self._database = database

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
        purpose = _vault_text(command.purpose, 200)
        app_id = _vault_text(command.app_id, 200) if command.app_id else ""
        now = int(time.time() * 1000)
        projection_id = "projection:" + hashlib.sha256(
            f"{command.record_id}:{command.target_group_id}:{command.target_bot_id}:{purpose}".encode()
        ).hexdigest()[:24]
        async with self._database.connect(user_id) as db:
            if app_id:
                await self._require_active_app(db, user_id, app_id)
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
                 purpose, command.expires_at, now, now),
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
        user_id = _user_id(command.scope)
        habit_key = safe_memory_text(command.habit_key, limit=200)
        record_id = "habit:" + hashlib.sha256(f"{user_id}:{habit_key}".encode()).hexdigest()[:24]
        statement = safe_memory_text(command.statement)
        now = int(time.time() * 1000)
        async with self._database.connect(user_id) as db:
            await db.execute(
                """INSERT INTO personal_records
                   (record_id,user_id,kind,content,speaker,subject,authority,sensitivity,status,
                    source_type,source_id,confidence,explicit,valid_from,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(record_id) DO UPDATE SET content=excluded.content,
                     updated_at=excluded.updated_at""",
                (record_id, user_id, "habit", statement, "", habit_key, "observed",
                 "private", "provisional", safe_memory_text(command.source_type, limit=200), f"habit:{habit_key}",
                 0.45, 0, command.observed_at, now, now),
            )
            await db.execute(
                "INSERT OR REPLACE INTO habit_evidence(record_id,source_type,source_key,context_kind,polarity,observed_at) VALUES(?,?,?,?,?,?)",
                (record_id, safe_memory_text(command.source_type, limit=200), safe_memory_text(command.source_id, limit=300),
                 safe_memory_text(command.context_kind, limit=200), safe_memory_text(command.polarity, limit=40), command.observed_at),
            )
            async with db.execute(
                """SELECT COUNT(*),COUNT(DISTINCT context_kind),MIN(observed_at),MAX(observed_at),
                          SUM(CASE WHEN polarity='support' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN polarity='contradict' THEN 1 ELSE 0 END)
                   FROM habit_evidence WHERE record_id=?""", (record_id,)
            ) as cur:
                samples, contexts, first_seen, last_seen, supporting, contradictions = await cur.fetchone()
            mature = (
                int(supporting or 0) >= 3 and int(contexts or 0) >= 2
                and int(last_seen or 0) - int(first_seen or 0) >= 14 * 86_400_000
                and int(contradictions or 0) == 0
            )
            await db.execute(
                "UPDATE personal_records SET status=?,confidence=?,updated_at=? WHERE record_id=?",
                ("active" if mature else "provisional", min(0.95, 0.45 + 0.1 * int(samples or 0)) if mature else 0.45, now, record_id),
            )
            await db.commit()
        return record_id

    async def format_projected_context(self, command: FormatProjectedContext) -> str:
        user_id = _user_id(command.scope)
        if command.scope.group_id is None:
            return ""
        purpose = _vault_text(command.purpose, 200)
        app_id = _vault_text(command.app_id, 200) if command.app_id else ""
        now = int(time.time() * 1000)
        async with self._database.connect(user_id) as db:
            if app_id:
                await self._require_active_app(db, user_id, app_id)
            async with db.execute(
                """SELECT r.record_id,p.projection_id,r.kind,r.content,r.authority,r.confidence,r.status,r.explicit
                   FROM personal_projections p JOIN personal_records r ON r.record_id=p.record_id
                   WHERE r.user_id=? AND p.group_id=? AND (p.bot_id IS NULL OR p.bot_id=?) AND p.purpose=?
                     AND p.status='active' AND r.status IN ('active','provisional') AND r.sensitivity!='secret'
                     AND (p.expires_at IS NULL OR p.expires_at>?)
                   ORDER BY r.explicit DESC,r.confidence DESC LIMIT 100""",
                (user_id, command.scope.group_id, command.scope.bot_id, purpose, now),
            ) as cur:
                rows = await cur.fetchall()
            selected_rows = []
            used = 0
            for row in rows:
                line = f"- [{row[2]}/{row[4]}] {safe_memory_text(row[3], limit=2000)}"
                if used + len(line) > command.char_budget:
                    break
                selected_rows.append(row)
                used += len(line)
            if selected_rows:
                await db.executemany(
                    """INSERT INTO personal_memory_usage_events
                       (user_id,record_id,projection_id,group_id,bot_id,session_id,purpose,used_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    [(user_id, row[0], row[1], command.scope.group_id, command.scope.bot_id,
                      safe_memory_text(command.scope.run_id or "", limit=200), _vault_text(command.purpose, 200), now) for row in selected_rows],
                )
            await db.commit()
        chunks: list[str] = []
        used = 0
        for row in selected_rows:
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
        return {"expired_projections": cur.rowcount, "schema_version": PERSONAL_SCHEMA_VERSION}

    async def export(self, scope: MemoryScope, *, cursor: int = 0, limit: int = _EXPORT_LIMIT) -> Mapping[str, Any]:
        user_id = _user_id(scope)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _EXPORT_LIMIT:
            raise ValueError(f"limit must be between 1 and {_EXPORT_LIMIT}")
        async with self._database.connect(user_id) as db:
            async with db.execute("SELECT record_id,kind,content,speaker,subject,authority,sensitivity,status,source_type,source_id,confidence,explicit,valid_from,valid_to FROM personal_records WHERE user_id=? ORDER BY created_at,record_id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                records = await cur.fetchall()
            async with db.execute("SELECT p.projection_id,p.record_id,p.group_id,p.bot_id,p.purpose,p.status,p.expires_at FROM personal_projections p JOIN personal_records r ON r.record_id=p.record_id WHERE r.user_id=? ORDER BY p.created_at,p.projection_id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                projections = await cur.fetchall()
            async with db.execute("SELECT usage_id,record_id,projection_id,group_id,bot_id,session_id,purpose,used_at FROM personal_memory_usage_events WHERE user_id=? ORDER BY used_at,usage_id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                usage_events = await cur.fetchall()
            async with db.execute("SELECT app_id,name,status,created_at,updated_at FROM personal_apps WHERE user_id=? ORDER BY app_id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                apps = await cur.fetchall()
            async with db.execute("SELECT rule_id,subject_type,subject_id,object_type,object_id,action,effect,created_at FROM personal_access_control_actions WHERE user_id=? ORDER BY rule_id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                acl_rules = await cur.fetchall()
            async with db.execute("SELECT audit_id,actor_id,scope_kind,group_id,bot_id,action,allowed,reason,created_at FROM personal_acl_audit_events WHERE user_id=? ORDER BY audit_id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                audit_events = await cur.fetchall()
            async with db.execute("SELECT id,record_id,source_type,source_key,context_kind,polarity,observed_at FROM habit_evidence WHERE record_id IN (SELECT record_id FROM personal_records WHERE user_id=?) ORDER BY id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                habit_evidence = await cur.fetchall()
            async with db.execute("SELECT audit_id,actor_id,operation,record_id,projection_id,created_at FROM personal_deletion_audit_events WHERE user_id=? ORDER BY audit_id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                deletion_audits = await cur.fetchall()
            async with db.execute("SELECT conflict_id,migration_version,resolution_status,resolved_at,resolved_by,content_hash,source_type,source_id,kind,canonical_record_id,conflicting_record_id,content,authority,explicit,confidence,valid_from,created_at FROM personal_migration_conflicts WHERE user_id=? ORDER BY created_at,conflict_id LIMIT ? OFFSET ?", (user_id, limit, cursor)) as cur:
                migration_conflicts = await cur.fetchall()
            counts = {}
            for name, table, predicate in (
                ("records", "personal_records", "user_id=?"),
                ("usage_events", "personal_memory_usage_events", "user_id=?"),
                ("apps", "personal_apps", "user_id=?"),
                ("acl_audit_events", "personal_acl_audit_events", "user_id=?"),
                ("acl_rules", "personal_access_control_actions", "user_id=?"),
                ("deletion_audit_events", "personal_deletion_audit_events", "user_id=?"),
                ("migration_conflicts", "personal_migration_conflicts", "user_id=?"),
            ):
                async with db.execute(f"SELECT COUNT(*) FROM {table} WHERE {predicate}", (user_id,)) as cur:
                    counts[name] = int((await cur.fetchone())[0])
            async with db.execute("SELECT COUNT(*) FROM personal_projections p JOIN personal_records r ON r.record_id=p.record_id WHERE r.user_id=?", (user_id,)) as cur:
                counts["projections"] = int((await cur.fetchone())[0])
            async with db.execute("SELECT COUNT(*) FROM habit_evidence WHERE record_id IN (SELECT record_id FROM personal_records WHERE user_id=?)", (user_id,)) as cur:
                counts["habit_evidence"] = int((await cur.fetchone())[0])
        fields = ("record_id","kind","content","speaker","subject","authority","sensitivity","status","source_type","source_id","confidence","explicit","valid_from","valid_to")
        pfields = ("projection_id","record_id","group_id","bot_id","purpose","status","expires_at")
        ufields = ("usage_id","record_id","projection_id","group_id","bot_id","session_id","purpose","used_at")
        afields = ("app_id","name","status","created_at","updated_at")
        rfields = ("rule_id","subject_type","subject_id","object_type","object_id","action","effect","created_at")
        efields = ("audit_id","actor_id","scope_kind","group_id","bot_id","action","allowed","reason","created_at")
        hfields = ("id","record_id","source_type","source_key","context_kind","polarity","observed_at")
        dfields = ("audit_id","actor_id","operation","record_id","projection_id","created_at")
        cfields = ("conflict_id","migration_version","resolution_status","resolved_at","resolved_by","content_hash","source_type","source_id","kind","canonical_record_id","conflicting_record_id","content","authority","explicit","confidence","valid_from","created_at")
        safe_rows = lambda fields, rows: [
            {key: (safe_memory_text(value, limit=1000) if isinstance(value, str) else value)
             for key, value in zip(fields, row)} for row in rows
        ]
        return {"schema_version": PERSONAL_SCHEMA_VERSION, "user_id": user_id,
                "records": safe_rows(fields, records), "projections": safe_rows(pfields, projections),
                "usage_events": safe_rows(ufields, usage_events), "apps": safe_rows(afields, apps),
                "acl_rules": safe_rows(rfields, acl_rules), "acl_audit_events": safe_rows(efields, audit_events),
                "habit_evidence": safe_rows(hfields, habit_evidence), "deletion_audit_events": safe_rows(dfields, deletion_audits),
                "migration_conflicts": safe_rows(cfields, migration_conflicts),
                "export": {
                    "page_size": limit, "cursor": cursor,
                    "next_cursor": cursor + limit if any(total > cursor + limit for total in counts.values()) else None,
                    "has_more": {name: total > cursor + limit for name, total in counts.items()},
                    "total_counts": counts,
                }}

    async def get_record_impact(self, scope: MemoryScope, record_id: str) -> Mapping[str, Any]:
        user_id = _user_id(scope)
        async with self._database.connect(user_id) as db:
            async with db.execute("SELECT projection_id,group_id,bot_id,purpose,status,expires_at FROM personal_projections WHERE record_id=? ORDER BY created_at DESC", (record_id,)) as cur:
                rows = await cur.fetchall()
            async with db.execute(
                """SELECT projection_id,group_id,bot_id,session_id,purpose,used_at
                   FROM personal_memory_usage_events
                   WHERE user_id=? AND record_id=? ORDER BY used_at DESC""",
                (user_id, record_id),
            ) as cur:
                usage_rows = await cur.fetchall()
        projections = [{"projection_id": r[0], "group_id": r[1], "bot_id": r[2], "purpose": r[3], "status": r[4], "expires_at": r[5]} for r in rows]
        usage_events = [{"projection_id": r[0], "group_id": r[1], "bot_id": r[2], "session_id": r[3], "purpose": r[4], "used_at": r[5]} for r in usage_rows]
        return {"record_id": record_id, "active_projections": [p for p in projections if p["status"] == "active"], "projections": projections, "usage_events": usage_events, "affected_group_ids": sorted({p["group_id"] for p in projections} | {r["group_id"] for r in usage_events}), "affected_session_ids": sorted({r["session_id"] for r in usage_events if r["session_id"]})}

    async def delete(self, scope: MemoryScope) -> bool:
        user_id = _user_id(scope)
        return await self._database.delete_vault(user_id)

    async def delete_record(self, scope: MemoryScope, record_id: str) -> bool:
        user_id = _user_id(scope)
        async with self._database.connect(user_id) as db:
            await db.execute("DELETE FROM personal_memory_usage_events WHERE user_id=? AND record_id=?", (user_id, record_id))
            cur = await db.execute("DELETE FROM personal_records WHERE user_id=? AND record_id=?", (user_id, record_id))
            if cur.rowcount:
                await db.execute(
                    "INSERT INTO personal_deletion_audit_events(user_id,actor_id,operation,record_id,projection_id,created_at) VALUES(?,?,?,?,?,?)",
                    (user_id, safe_memory_text(scope.actor_id, limit=200), "delete_record", safe_memory_text(record_id, limit=200), None, int(time.time() * 1000)),
                )
            await db.commit()
        return cur.rowcount > 0

    async def revoke_projection(self, scope: MemoryScope, projection_id: str) -> bool:
        user_id = _user_id(scope)
        async with self._database.connect(user_id) as db:
            async with db.execute(
                "SELECT record_id FROM personal_projections WHERE projection_id=? AND record_id IN (SELECT record_id FROM personal_records WHERE user_id=?)",
                (projection_id, user_id),
            ) as check:
                record_row = await check.fetchone()
            cur = await db.execute("DELETE FROM personal_projections WHERE projection_id=? AND record_id IN (SELECT record_id FROM personal_records WHERE user_id=?)", (projection_id, user_id))
            if cur.rowcount and record_row:
                await db.execute(
                    "INSERT INTO personal_deletion_audit_events(user_id,actor_id,operation,record_id,projection_id,created_at) VALUES(?,?,?,?,?,?)",
                    (user_id, safe_memory_text(scope.actor_id, limit=200), "revoke_projection", safe_memory_text(record_row[0], limit=200), safe_memory_text(projection_id, limit=200), int(time.time() * 1000)),
                )
            await db.commit()
        return cur.rowcount > 0

    async def _upsert_record(self, *, user_id: int, kind: str, content: str, source_type: str, source_id: str, speaker: str, subject: str, authority: str, sensitivity: str, confidence: float, explicit: bool) -> str:
        if kind not in _KINDS or sensitivity not in _SENSITIVITIES:
            raise ValueError("unsupported personal memory kind or sensitivity")
        safe = safe_memory_text(content)
        safe_speaker = safe_memory_text(speaker, limit=500)
        safe_subject = safe_memory_text(subject, limit=500)
        safe_source_type = safe_memory_text(source_type, limit=200)
        safe_source_id = safe_memory_text(source_id, limit=500)
        desired_authority = "user_statement" if explicit else authority
        desired_status = "active" if explicit else "provisional"
        now = int(time.time() * 1000)
        async def _find_existing(db):
            if not safe_source_id:
                return None
            async with db.execute(
                "SELECT record_id,sensitivity,content FROM personal_records WHERE user_id=? AND source_type=? AND source_id=? AND kind=? ORDER BY created_at,record_id",
                (user_id, safe_source_type, safe_source_id, kind),
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                if str(row[2]) == safe:
                    return row
            return None
        record_id = "personal:" + hashlib.sha256(
            f"{user_id}:{safe_source_type}:{safe_source_id}:{kind}:{safe}".encode()
        ).hexdigest()[:24]
        async with self._database.connect(user_id) as db:
            existing_row = await _find_existing(db)
            if existing_row:
                record_id = str(existing_row[0])
            existing_sensitivity = str(existing_row[1]) if existing_row else None
            strongest_sensitivity = _strongest_sensitivity(existing_sensitivity, sensitivity)
            await db.execute(
                """INSERT INTO personal_records(record_id,user_id,kind,content,speaker,subject,authority,sensitivity,status,source_type,source_id,confidence,explicit,valid_from,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(record_id) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at,
                     confidence=MAX(personal_records.confidence,excluded.confidence),
                     explicit=MAX(personal_records.explicit,excluded.explicit),
                     authority=CASE WHEN excluded.explicit=1 THEN 'user_statement' ELSE personal_records.authority END,
                     status=CASE WHEN excluded.explicit=1 THEN 'active' ELSE personal_records.status END,
                     speaker=CASE WHEN excluded.explicit=1 THEN excluded.speaker ELSE personal_records.speaker END,
                     subject=CASE WHEN excluded.explicit=1 THEN excluded.subject ELSE personal_records.subject END,
                     sensitivity=?""",
                (record_id,user_id,kind,safe,safe_speaker,safe_subject,desired_authority,strongest_sensitivity,desired_status,safe_source_type,safe_source_id,max(0,min(1,confidence)),int(explicit),now,now,now, strongest_sensitivity),
            )
            if strongest_sensitivity == "secret" and existing_sensitivity != "secret":
                await db.execute(
                    """UPDATE personal_projections SET status='revoked',updated_at=?
                       WHERE record_id=? AND status='active'""",
                    (now, record_id),
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


def _normalize_access_rule_key(*values: str) -> list[str]:
    normalized = [safe_memory_text(value, limit=200).strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError("access rule key fields cannot be empty")
    return normalized


def _vault_text(value: Any, limit: int) -> str:
    normalized = safe_memory_text(value, limit=limit).strip()
    if not normalized:
        raise ValueError("Vault text fields cannot be empty")
    return normalized


def _strongest_sensitivity(existing: str | None, requested: str) -> str:
    rank = {"private": 0, "restricted": 1, "secret": 2}
    if requested not in rank or (existing is not None and existing not in rank):
        raise ValueError("unsupported sensitivity")
    return requested if existing is None or rank[requested] >= rank[existing] else existing
