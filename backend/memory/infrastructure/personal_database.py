"""Personal Vault storage owned by canonical Memory infrastructure."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

import aiosqlite
import fcntl

from memory.contracts.versions import PERSONAL_SCHEMA_VERSION
from memory.domain.safety import safe_memory_text


_LOCKS: dict[int, asyncio.Lock] = {}
logger = logging.getLogger(__name__)
_MAX_SWEEPER_MARKERS = 100
_FAILED_MARKER_RETENTION_SECONDS = 7 * 24 * 60 * 60
_REQUIRED_COLUMNS = {
    "personal_schema_version": {"version", "applied_at"},
    "personal_records": {"record_id", "user_id", "kind", "content", "speaker", "subject", "authority", "sensitivity", "status", "source_type", "source_id", "confidence", "explicit", "valid_from", "valid_to", "created_at", "updated_at"},
    "personal_projections": {"projection_id", "record_id", "group_id", "bot_id", "purpose", "status", "expires_at", "created_at", "updated_at"},
    "personal_memory_usage_events": {"usage_id", "user_id", "record_id", "projection_id", "group_id", "bot_id", "session_id", "purpose", "used_at"},
    "personal_apps": {"app_id", "user_id", "name", "status", "created_at", "updated_at"},
    "personal_acl_audit_events": {"audit_id", "user_id", "actor_id", "scope_kind", "group_id", "bot_id", "action", "allowed", "reason", "created_at"},
    "personal_access_control_actions": {"rule_id", "user_id", "subject_type", "subject_id", "object_type", "object_id", "action", "effect", "created_at"},
    "habit_evidence": {"id", "record_id", "source_type", "source_key", "context_kind", "polarity", "observed_at"},
    "personal_deletion_audit_events": {"audit_id", "user_id", "actor_id", "operation", "record_id", "projection_id", "created_at"},
    "personal_migration_conflicts": {"conflict_id", "user_id", "migration_version", "resolution_status", "resolved_at", "resolved_by", "content_hash", "source_type", "source_id", "kind", "canonical_record_id", "conflicting_record_id", "content", "authority", "explicit", "confidence", "valid_from", "created_at"},
}

_DDL = (
    """CREATE TABLE IF NOT EXISTS personal_schema_version (
       version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS personal_records (
       record_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,
       content TEXT NOT NULL,speaker TEXT NOT NULL DEFAULT '',subject TEXT NOT NULL DEFAULT '',
       authority TEXT NOT NULL,sensitivity TEXT NOT NULL DEFAULT 'private',
       status TEXT NOT NULL DEFAULT 'active',source_type TEXT NOT NULL,source_id TEXT NOT NULL DEFAULT '',
       confidence REAL NOT NULL DEFAULT 0.5,explicit INTEGER NOT NULL DEFAULT 0,
       valid_from INTEGER NOT NULL,valid_to INTEGER,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS personal_projections (
       projection_id TEXT PRIMARY KEY,record_id TEXT NOT NULL,group_id INTEGER NOT NULL,bot_id INTEGER,
       purpose TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',expires_at INTEGER,
       created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
       UNIQUE(record_id,group_id,bot_id,purpose),
       FOREIGN KEY(record_id) REFERENCES personal_records(record_id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS personal_memory_usage_events (
       usage_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,record_id TEXT NOT NULL,
       projection_id TEXT NOT NULL,group_id INTEGER NOT NULL,bot_id INTEGER,session_id TEXT NOT NULL DEFAULT '',
       purpose TEXT NOT NULL,used_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS personal_apps (
       app_id TEXT NOT NULL,user_id INTEGER NOT NULL,name TEXT NOT NULL,
       status TEXT NOT NULL DEFAULT 'active',created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
       PRIMARY KEY(user_id,app_id))""",
    """CREATE TABLE IF NOT EXISTS personal_acl_audit_events (
       audit_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,actor_id TEXT NOT NULL,
       scope_kind TEXT NOT NULL,group_id INTEGER,bot_id INTEGER,action TEXT NOT NULL,
       allowed INTEGER NOT NULL,reason TEXT NOT NULL,created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS personal_deletion_audit_events (
       audit_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
       actor_id TEXT NOT NULL,operation TEXT NOT NULL,record_id TEXT,
       projection_id TEXT,created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS personal_migration_conflicts (
       conflict_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,migration_version INTEGER NOT NULL,
       resolution_status TEXT NOT NULL DEFAULT 'unresolved',resolved_at INTEGER,
       resolved_by TEXT,content_hash TEXT NOT NULL,source_type TEXT NOT NULL,
       source_id TEXT NOT NULL,kind TEXT NOT NULL,canonical_record_id TEXT NOT NULL,
       conflicting_record_id TEXT NOT NULL,content TEXT NOT NULL,authority TEXT NOT NULL,
       explicit INTEGER NOT NULL,confidence REAL NOT NULL,valid_from INTEGER NOT NULL,
       created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS personal_access_control_actions (
       rule_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,subject_type TEXT NOT NULL,
       subject_id TEXT NOT NULL,object_type TEXT NOT NULL,object_id TEXT NOT NULL,action TEXT NOT NULL,
       effect TEXT NOT NULL,created_at INTEGER NOT NULL,
       UNIQUE(user_id,subject_type,subject_id,object_type,object_id,action))""",
    """CREATE TABLE IF NOT EXISTS habit_evidence (
       id INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,source_type TEXT NOT NULL DEFAULT '',source_key TEXT NOT NULL,
       context_kind TEXT NOT NULL,polarity TEXT NOT NULL,observed_at INTEGER NOT NULL,
       UNIQUE(record_id,source_type,source_key),
       FOREIGN KEY(record_id) REFERENCES personal_records(record_id) ON DELETE CASCADE)""",
)


class PersonalVaultDatabase:
    @staticmethod
    def _path(user_id: int) -> Path:
        from workspace import layout
        return Path(layout.personal_dir(user_id)) / "knowledge.db"

    @asynccontextmanager
    async def connect(self, user_id: int) -> AsyncIterator[aiosqlite.Connection]:
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        lock = _LOCKS.setdefault(user_id, asyncio.Lock())
        async with lock:
            path = self._path(user_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = await asyncio.to_thread(os.open, str(path.parent), os.O_RDONLY)
            await asyncio.to_thread(fcntl.flock, lock_fd, fcntl.LOCK_EX)
            try:
                async with aiosqlite.connect(str(path), timeout=15.0) as db:
                    await db.execute("PRAGMA busy_timeout=15000")
                    await db.execute("PRAGMA foreign_keys=ON")
                    async with db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='personal_schema_version'"
                    ) as cursor:
                        has_version_table = await cursor.fetchone()
                    if has_version_table:
                        async with db.execute("PRAGMA table_info(personal_schema_version)") as cursor:
                            version_columns = {str(row[1]) for row in await cursor.fetchall()}
                        if {"version", "applied_at"} - version_columns:
                            raise RuntimeError("Personal Vault schema version table is malformed")
                        async with db.execute(
                            "SELECT COALESCE(MAX(version), 0) FROM personal_schema_version"
                        ) as cursor:
                            current = int((await cursor.fetchone())[0])
                    else:
                        current = 0
                    if current > PERSONAL_SCHEMA_VERSION:
                        raise RuntimeError(
                            f"Personal Vault schema version {current} is newer than supported {PERSONAL_SCHEMA_VERSION}"
                        )
                    await db.execute("PRAGMA journal_mode=WAL")
                    for statement in _DDL:
                        await db.execute(statement)
                    if current < 1:
                        await db.execute("INSERT INTO personal_schema_version(version, applied_at) VALUES(1, strftime('%s','now') * 1000)")
                    projection_fk = await _has_cascade_fk(db, "personal_projections", "personal_records")
                    habit_fk = await _has_cascade_fk(db, "habit_evidence", "personal_records")
                    habit_source_type = await _has_column(db, "habit_evidence", "source_type")
                    if current < 2 or not projection_fk or not habit_fk:
                        await db.execute("DELETE FROM personal_projections WHERE record_id NOT IN (SELECT record_id FROM personal_records)")
                        await db.execute("DELETE FROM personal_memory_usage_events WHERE record_id NOT IN (SELECT record_id FROM personal_records)")
                        await db.execute("DELETE FROM habit_evidence WHERE record_id NOT IN (SELECT record_id FROM personal_records)")
                        await _rebuild_fk_tables(db)
                        await db.execute("CREATE INDEX IF NOT EXISTS idx_personal_usage_record ON personal_memory_usage_events(user_id,record_id,used_at)")
                        await db.execute("CREATE INDEX IF NOT EXISTS idx_personal_acl_audit_user ON personal_acl_audit_events(user_id,created_at)")
                        if current < 2:
                            await db.execute("INSERT INTO personal_schema_version(version, applied_at) VALUES(2, strftime('%s','now') * 1000)")
                    if current < 3 or not habit_source_type:
                        await _rebuild_fk_tables(db)
                        if current < 3:
                            await db.execute("INSERT INTO personal_schema_version(version, applied_at) VALUES(3, strftime('%s','now') * 1000)")
                    if await _missing_columns(db, "personal_migration_conflicts"):
                        await _upgrade_conflict_table(db)
                    if current < 4:
                        await _merge_duplicate_source_records(db)
                        await db.execute("INSERT INTO personal_schema_version(version, applied_at) VALUES(4, strftime('%s','now') * 1000)")
                    if current < 5:
                        await db.execute("INSERT INTO personal_schema_version(version, applied_at) VALUES(5, strftime('%s','now') * 1000)")
                    await _validate_shape(db)
                    async with db.execute("PRAGMA foreign_key_check") as cur:
                        violations = await cur.fetchall()
                    if violations:
                        raise RuntimeError(f"Personal Vault foreign_key_check failed: {violations!r}")
                    await db.commit()
                    yield db
            finally:
                await asyncio.to_thread(fcntl.flock, lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    async def delete_vault(self, user_id: int) -> Mapping[str, Any]:
        """Delete every personal artifact and remove the physical Vault file."""
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        path = self._path(user_id)
        lock = _LOCKS.setdefault(user_id, asyncio.Lock())
        async with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = await asyncio.to_thread(os.open, str(path.parent), os.O_RDONLY)
            locked = False
            audit_id = None
            intent_marker: Path | None = None
            try:
                await asyncio.to_thread(fcntl.flock, lock_fd, fcntl.LOCK_EX)
                locked = True
                operation_id = f"vault-delete:{uuid.uuid4().hex}"
                intent_marker = Path(f"{path}.delete.{operation_id}.intent")
                committed_marker = Path(f"{path}.delete.{operation_id}.committed")
                await asyncio.to_thread(intent_marker.write_text, operation_id, encoding="utf-8")
                audit_enabled = type(self) is PersonalVaultDatabase
                try:
                    audit_id, operation_id = await _create_central_vault_deletion(
                        user_id, operation_id=operation_id, marker_path=str(intent_marker), enabled=audit_enabled
                    )
                except Exception:
                    logger.exception("central Personal Vault deletion audit unavailable")
                    # The local intent is the durable outbox when the central
                    # database is unavailable.  Continue with privacy deletion.
                if not path.exists():
                    await _finish_central_vault_deletion(audit_id, "not_found", enabled=audit_enabled)
                    intent_marker.unlink(missing_ok=True)
                    return {"deleted": False, "audit_pending": False, "audit_status": "not_found"}
                await _finish_central_vault_deletion(
                    audit_id, "local_delete_started", enabled=audit_enabled
                )
                async with aiosqlite.connect(str(path), timeout=15.0) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=15000")
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("DELETE FROM personal_projections")
                    await db.execute("DELETE FROM personal_memory_usage_events")
                    await db.execute("DELETE FROM habit_evidence")
                    await db.execute("DELETE FROM personal_records")
                    await db.execute("DELETE FROM personal_apps")
                    await db.execute("DELETE FROM personal_access_control_actions")
                    await db.execute("DELETE FROM personal_acl_audit_events")
                    await db.execute("DELETE FROM personal_migration_conflicts")
                    await db.commit()
                    await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                await asyncio.to_thread(os.replace, intent_marker, committed_marker)
                await _finish_central_vault_deletion(
                    audit_id, "local_delete_committed", local_commit_marker=str(committed_marker),
                    enabled=audit_enabled
                )
                # The connection is closed before unlinking SQLite artifacts.
                for suffix in ("", "-wal", "-shm"):
                    candidate = Path(str(path) + suffix)
                    try:
                        candidate.unlink()
                    except FileNotFoundError:
                        pass
                try:
                    Path(str(path) + ".lock").unlink()
                except FileNotFoundError:
                    pass
                audit_pending = audit_enabled and (audit_id is None or not await _finish_central_vault_deletion(
                    audit_id, "completed", physical_delete_confirmed=True,
                    enabled=audit_enabled
                ))
                if not audit_pending:
                    try:
                        committed_marker.unlink()
                    except FileNotFoundError:
                        pass
                return {"deleted": True, "audit_pending": audit_pending, "audit_status": "pending" if audit_pending else "completed"}
            except Exception as exc:
                await _finish_central_vault_deletion(
                    audit_id, "failed", error=str(exc), enabled=type(self) is PersonalVaultDatabase
                )
                if intent_marker is not None:
                    failed_marker = Path(f"{intent_marker.with_suffix('')}.failed")
                    try:
                        await asyncio.to_thread(os.replace, intent_marker, failed_marker)
                        await asyncio.to_thread(
                            failed_marker.write_text,
                            f"{operation_id}\n{int(time.time())}\n{safe_memory_text(str(exc), limit=1000)}",
                            encoding="utf-8",
                        )
                    except FileNotFoundError:
                        pass
                raise
            finally:
                if locked:
                    await asyncio.to_thread(fcntl.flock, lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)


async def _rebuild_fk_tables(db: aiosqlite.Connection) -> None:
    await db.execute("ALTER TABLE personal_projections RENAME TO personal_projections_v1")
    await db.execute(_DDL[3 - 1])
    await db.execute("""INSERT INTO personal_projections
        SELECT projection_id,record_id,group_id,bot_id,purpose,status,expires_at,created_at,updated_at
        FROM personal_projections_v1""")
    await db.execute("DROP TABLE personal_projections_v1")
    await db.execute("ALTER TABLE habit_evidence RENAME TO habit_evidence_v1")
    await db.execute(_DDL[-1])
    async with db.execute("PRAGMA table_info(habit_evidence_v1)") as cur:
        columns = {str(row[1]) for row in await cur.fetchall()}
    if "source_type" in columns:
        await db.execute("INSERT INTO habit_evidence SELECT id,record_id,source_type,source_key,context_kind,polarity,observed_at FROM habit_evidence_v1")
    else:
        await db.execute("INSERT INTO habit_evidence(id,record_id,source_type,source_key,context_kind,polarity,observed_at) SELECT id,record_id,'',source_key,context_kind,polarity,observed_at FROM habit_evidence_v1")
    await db.execute("DROP TABLE habit_evidence_v1")
    async with db.execute("PRAGMA foreign_key_check") as cur:
        violations = await cur.fetchall()
    if violations:
        raise RuntimeError(f"Personal Vault foreign_key_check failed: {violations!r}")


async def _has_cascade_fk(db: aiosqlite.Connection, table: str, target: str) -> bool:
    async with db.execute(f"PRAGMA foreign_key_list({table})") as cur:
        rows = await cur.fetchall()
    return any(str(row[2]) == target and str(row[6]).upper() == "CASCADE" for row in rows)


async def _has_column(db: aiosqlite.Connection, table: str, column: str) -> bool:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        return column in {str(row[1]) for row in await cur.fetchall()}


async def _missing_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        actual = {str(row[1]) for row in await cur.fetchall()}
    return _REQUIRED_COLUMNS[table] - actual


async def _merge_duplicate_source_records(db: aiosqlite.Connection) -> None:
    async with db.execute(
        """SELECT user_id,source_type,source_id,kind,COUNT(*) FROM personal_records
           WHERE source_id<>'' GROUP BY user_id,source_type,source_id,kind HAVING COUNT(*)>1"""
    ) as cur:
        groups = await cur.fetchall()
    for user_id, source_type, source_id, kind, _ in groups:
        async with db.execute(
            """SELECT record_id,content,authority,sensitivity,status,confidence,explicit,
                      valid_from,created_at,updated_at
               FROM personal_records
               WHERE user_id=? AND source_type=? AND source_id=? AND kind=?
               ORDER BY created_at,record_id""",
            (user_id, source_type, source_id, kind),
        ) as cur:
            rows = await cur.fetchall()
        by_content: dict[str, list[str]] = {}
        for row in rows:
            by_content.setdefault(safe_memory_text(row[1]), []).append(str(row[0]))
        for ids in by_content.values():
            if len(ids) > 1:
                await _merge_records(db, ids)
        # Different statements sharing a source are valid history, not records
        # to delete.  Preserve every record and retain an explicit migration
        # conflict for operators/auditors.
        if len(by_content) > 1:
            authority_rank = {"observed": 0, "third_party": 1, "user_statement": 2}
            canonical = max(rows, key=lambda row: (
                int(row[6]), authority_rank.get(str(row[2]), 0), float(row[5]),
                int(row[9]), int(row[8]), str(row[0]),
            ))[0]
            canonical_content = safe_memory_text(next(row[1] for row in rows if row[0] == canonical))
            for row in rows:
                if str(row[0]) != str(canonical) and safe_memory_text(row[1]) != canonical_content:
                    await _record_migration_conflict(
                        db, user_id=int(user_id), source_type=str(source_type),
                        source_id=str(source_id), kind=str(kind),
                        canonical_record_id=str(canonical), row=row,
                    )


async def _record_migration_conflict(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    source_type: str,
    source_id: str,
    kind: str,
    canonical_record_id: str,
    row: tuple[object, ...],
) -> None:
    conflicting_record_id = str(row[0])
    content = safe_memory_text(row[1])
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    conflict_id = "conflict:" + hashlib.sha256(
        f"4:{canonical_record_id}:{conflicting_record_id}:{content_hash}".encode()
    ).hexdigest()[:24]
    await db.execute(
        """INSERT OR IGNORE INTO personal_migration_conflicts
           (conflict_id,user_id,migration_version,resolution_status,resolved_at,
            resolved_by,content_hash,source_type,source_id,kind,canonical_record_id,
            conflicting_record_id,content,authority,explicit,confidence,valid_from,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (conflict_id, user_id, 4, "unresolved", None, None, content_hash,
         source_type, source_id, kind, canonical_record_id, conflicting_record_id,
         content, str(row[2]), int(row[6]), float(row[5]), int(row[7]), int(row[9])),
    )


async def _merge_records(db: aiosqlite.Connection, record_ids: list[str]) -> str | None:
    if not record_ids:
        return None
    placeholders = ",".join("?" for _ in record_ids)
    async with db.execute(
        f"""SELECT record_id,content,authority,sensitivity,status,confidence,explicit,valid_from,created_at,updated_at
            FROM personal_records WHERE record_id IN ({placeholders})""",
        record_ids,
    ) as cur:
        rows = await cur.fetchall()
    authority_rank = {"observed": 0, "third_party": 1, "user_statement": 2}
    winner = max(rows, key=lambda row: (
        int(row[6]), authority_rank.get(str(row[2]), 0), float(row[5]), int(row[9]), int(row[8]), str(row[0])
    ))
    canonical = str(winner[0])
    strongest_authority = max((str(row[2]) for row in rows), key=lambda value: authority_rank.get(value, 0))
    strongest_status = "active" if any(str(row[4]) == "active" for row in rows) else str(winner[4])
    strongest_explicit = max(int(row[6]) for row in rows)
    strongest_confidence = max(float(row[5]) for row in rows)
    earliest_valid_from = min(int(row[7]) for row in rows)
    earliest_created_at = min(int(row[8]) for row in rows)
    latest_updated_at = max(int(row[9]) for row in rows)
    async with db.execute(
        f"SELECT sensitivity FROM personal_records WHERE record_id IN ({placeholders})",
        record_ids,
    ) as cur:
        sensitivities = [str(row[0]) for row in await cur.fetchall()]
    strongest = max(sensitivities, key={"private": 0, "restricted": 1, "secret": 2}.get) if sensitivities else "private"
    await db.execute(
        """UPDATE personal_records SET content=?,authority=?,sensitivity=?,status=?,confidence=?,explicit=?,
           valid_from=?,created_at=?,updated_at=? WHERE record_id=?""",
        (safe_memory_text(winner[1]), strongest_authority, strongest, strongest_status,
         strongest_confidence, strongest_explicit, earliest_valid_from, earliest_created_at,
         latest_updated_at, canonical),
    )
    for row in rows:
        conflicting_id = str(row[0])
        if conflicting_id == canonical:
            continue
    if strongest == "secret":
        await db.execute("UPDATE personal_projections SET status='revoked' WHERE record_id IN ({}) AND status='active'".format(",".join("?" for _ in record_ids)), record_ids)
    for duplicate in record_ids:
        if duplicate == canonical:
            continue
        await db.execute("UPDATE personal_memory_usage_events SET record_id=? WHERE record_id=?", (canonical, duplicate))
        await db.execute("INSERT OR IGNORE INTO personal_projections(projection_id,record_id,group_id,bot_id,purpose,status,expires_at,created_at,updated_at) SELECT projection_id,?,group_id,bot_id,purpose,status,expires_at,created_at,updated_at FROM personal_projections WHERE record_id=?", (canonical, duplicate))
        if await _has_column(db, "habit_evidence", "source_type"):
            await db.execute("INSERT OR IGNORE INTO habit_evidence(record_id,source_type,source_key,context_kind,polarity,observed_at) SELECT ?,source_type,source_key,context_kind,polarity,observed_at FROM habit_evidence WHERE record_id=?", (canonical, duplicate))
        else:
            await db.execute("INSERT OR IGNORE INTO habit_evidence(record_id,source_key,context_kind,polarity,observed_at) SELECT ?,source_key,context_kind,polarity,observed_at FROM habit_evidence WHERE record_id=?", (canonical, duplicate))
        await db.execute("DELETE FROM personal_records WHERE record_id=?", (duplicate,))
    return canonical


async def _upgrade_conflict_table(db: aiosqlite.Connection) -> None:
    await db.execute("ALTER TABLE personal_migration_conflicts RENAME TO personal_migration_conflicts_v4")
    await db.execute(_DDL[7])
    async with db.execute("PRAGMA table_info(personal_migration_conflicts_v4)") as cur:
        columns = {str(row[1]) for row in await cur.fetchall()}
    async with db.execute("SELECT * FROM personal_migration_conflicts_v4") as cur:
        legacy_rows = await cur.fetchall()
    indexes = {name: index for index, name in enumerate(columns)}
    # PRAGMA column order is stable for SQLite tables, but use the names from
    # the table definition rather than assuming a particular legacy layout.
    async with db.execute("PRAGMA table_info(personal_migration_conflicts_v4)") as cur:
        indexes = {str(row[1]): int(row[0]) for row in await cur.fetchall()}
    for legacy in legacy_rows:
        def value(name: str, default: object = None) -> object:
            index = indexes.get(name)
            return legacy[index] if index is not None else default

        content = safe_memory_text(value("content", ""))
        await db.execute(
            """INSERT INTO personal_migration_conflicts
               (conflict_id,user_id,migration_version,resolution_status,resolved_at,resolved_by,
                content_hash,source_type,source_id,kind,canonical_record_id,conflicting_record_id,
                content,authority,explicit,confidence,valid_from,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(value("conflict_id", "")), int(value("user_id", 0)),
             int(value("migration_version", 4)), str(value("resolution_status", "unresolved")),
             value("resolved_at"), value("resolved_by"),
             str(value("content_hash") or hashlib.sha256(content.encode()).hexdigest()),
             str(value("source_type", "")), str(value("source_id", "")), str(value("kind", "")),
             str(value("canonical_record_id", "")), str(value("conflicting_record_id", "")),
             content, str(value("authority", "observed")), int(value("explicit", 0)),
             float(value("confidence", 0.0)), int(value("valid_from", 0)), int(value("created_at", 0))),
        )
    await db.execute("DROP TABLE personal_migration_conflicts_v4")


async def _create_central_vault_deletion(
    user_id: int, *, operation_id: str | None = None, marker_path: str | None = None,
    enabled: bool = True,
) -> tuple[int | None, str | None]:
    if not enabled:
        return None, None
    from db import global_db
    async with global_db() as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='personal_vault_deletion_audit'"
        ) as cur:
            if await cur.fetchone() is None:
                raise RuntimeError("central Personal Vault deletion audit schema is not ready")
        operation_id = operation_id or f"vault-delete:{uuid.uuid4().hex}"
        try:
            cur = await db.execute(
                "INSERT INTO personal_vault_deletion_audit(operation_id,user_id,operation,status,local_commit_marker,created_at) VALUES(?,?,?,?,?,strftime('%s','now') * 1000)",
                (operation_id, user_id, "delete_vault", "pending", marker_path),
            )
            await db.commit()
            return int(cur.lastrowid), operation_id
        except Exception as exc:
            # INSERT/COMMIT may have succeeded before the caller observed a
            # constraint or transport failure.  Treat operation_id as an
            # idempotency key and recover the committed row when possible.
            try:
                await db.rollback()
                async with db.execute(
                    "SELECT audit_id,user_id FROM personal_vault_deletion_audit WHERE operation_id=?",
                    (operation_id,),
                ) as cur:
                    existing = await cur.fetchone()
            except Exception:
                raise exc
            if existing is None or int(existing[1]) != user_id:
                raise exc
            return int(existing[0]), operation_id


async def _finish_central_vault_deletion(
    audit_id: int | None,
    status: str,
    *,
    error: str = "",
    local_commit_marker: str | None = None,
    physical_delete_confirmed: bool = False,
    enabled: bool = True,
) -> bool:
    if not enabled or audit_id is None:
        return True
    try:
        from db import global_db
        async with global_db() as db:
            await db.execute(
                """UPDATE personal_vault_deletion_audit
                   SET status=?, delete_started_at=CASE WHEN ?='local_delete_started' THEN strftime('%s','now') * 1000 ELSE delete_started_at END,
                       local_commit_marker=COALESCE(?,local_commit_marker), physical_delete_confirmed=CASE WHEN ? THEN 1 ELSE physical_delete_confirmed END,
                       completed_at=CASE WHEN ? IN ('completed','failed','not_found') THEN strftime('%s','now') * 1000 ELSE completed_at END,
                       last_error=? WHERE audit_id=?""",
                (status, status, local_commit_marker, physical_delete_confirmed, status,
                 safe_memory_text(error, limit=1000), audit_id),
            )
            await db.commit()
        return True
    except Exception:
        # The local deletion is already durable.  The pending central row is
        # the retryable outbox; callers must receive success plus pending state.
        return False


async def sweep_pending_vault_deletions(*, limit: int = 100) -> Mapping[str, int]:
    """Complete only deletions with an explicit local commit marker.

    A missing Vault path alone is never sufficient evidence: a pending request
    may not have started, or a new Vault may have been created.  The marker is
    written only after the local delete transaction commits and is retained
    until the central completion update succeeds.
    """
    from db import global_db
    scanned = completed = skipped = 0
    async with global_db() as db:
        async with db.execute(
            """SELECT audit_id,operation_id,user_id,local_commit_marker FROM personal_vault_deletion_audit
            WHERE status IN ('pending','local_delete_started','local_delete_committed')
            ORDER BY audit_id LIMIT ?""",
            (max(1, min(int(limit), 1000)),),
        ) as cur:
            rows = await cur.fetchall()
    known = {str(operation): (int(audit), int(user), marker) for audit, operation, user, marker in rows}
    candidates: dict[str, tuple[int, Path]] = {}
    for audit_id, _operation_id, user_id, marker_name in rows:
        if marker_name:
            candidates[str(_operation_id)] = (int(user_id), Path(str(marker_name)))
    personal_root = PersonalVaultDatabase._path(1).parents[1]

    def discover_markers() -> tuple[list[tuple[str, int, Path]], int]:
        discovered: list[tuple[str, int, Path]] = []
        failed_cleaned = 0
        try:
            user_dirs = os.scandir(personal_root)
        except FileNotFoundError:
            return discovered, failed_cleaned
        with user_dirs:
            for user_entry in user_dirs:
                if len(discovered) >= _MAX_SWEEPER_MARKERS or not user_entry.name.startswith("user_"):
                    continue
                try:
                    user_id = int(user_entry.name.removeprefix("user_"))
                except ValueError:
                    continue
                try:
                    marker_entries = os.scandir(user_entry.path)
                except OSError:
                    continue
                with marker_entries:
                    for marker_entry in marker_entries:
                        name = marker_entry.name
                        if name.endswith(".failed"):
                            try:
                                if time.time() - marker_entry.stat().st_mtime > _FAILED_MARKER_RETENTION_SECONDS:
                                    os.unlink(marker_entry.path)
                                    failed_cleaned += 1
                            except OSError:
                                pass
                            continue
                        if not name.startswith("knowledge.db.delete.") or not name.endswith(".committed"):
                            continue
                        operation_id = name.split(".delete.", 1)[1][:-len(".committed")]
                        discovered.append((operation_id, user_id, Path(marker_entry.path)))
                        if len(discovered) >= _MAX_SWEEPER_MARKERS:
                            break
        return discovered, failed_cleaned

    discovered, failed_cleaned = await asyncio.to_thread(discover_markers)
    if failed_cleaned:
        logger.info("Personal Vault sweeper removed %d expired failed markers", failed_cleaned)
    for operation_id, user_id, marker in discovered:
        # The central row may still point at the pre-commit intent path if its
        # local_delete_committed update was interrupted.  The committed marker
        # is stronger evidence and must replace that stale path.
        candidates[operation_id] = (user_id, marker)
    for operation_id, (user_id, marker) in list(candidates.items())[: max(1, min(int(limit), 1000))]:
        scanned += 1
        vault_path = PersonalVaultDatabase._path(user_id)
        if vault_path.exists() or not marker.exists():
            skipped += 1
            continue
        audit = known.get(operation_id)
        audit_id = audit[0] if audit else None
        if audit_id is None:
            try:
                audit_id, _ = await _create_central_vault_deletion(
                    user_id, operation_id=operation_id, marker_path=str(marker)
                )
            except Exception:
                skipped += 1
                continue
        if await _finish_central_vault_deletion(
            audit_id, "completed", local_commit_marker=str(marker), physical_delete_confirmed=True,
        ):
            completed += 1
            marker.unlink(missing_ok=True)
    return {"scanned": scanned, "completed": completed, "skipped": skipped}


async def _validate_shape(db: aiosqlite.Connection) -> None:
    for table, required in _REQUIRED_COLUMNS.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            actual = {str(row[1]) for row in await cur.fetchall()}
        missing = required - actual
        if missing:
            raise RuntimeError(f"Personal Vault table {table} is missing columns: {sorted(missing)}")
