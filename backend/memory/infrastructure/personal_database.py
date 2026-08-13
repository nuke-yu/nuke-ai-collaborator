"""Personal Vault storage owned by canonical Memory infrastructure."""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
import fcntl

from memory.contracts.versions import PERSONAL_SCHEMA_VERSION
from memory.domain.safety import safe_memory_text


_LOCKS: dict[int, asyncio.Lock] = {}
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
    "personal_migration_conflicts": {"conflict_id", "user_id", "source_type", "source_id", "kind", "canonical_record_id", "conflicting_record_id", "content", "authority", "explicit", "confidence", "valid_from", "created_at"},
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
       conflict_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,source_type TEXT NOT NULL,
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
            lock_path = Path(str(path) + ".lock")
            lock_handle = await asyncio.to_thread(lock_path.open, "a+")
            await asyncio.to_thread(fcntl.flock, lock_handle.fileno(), fcntl.LOCK_EX)
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
                    if current < 4:
                        await _merge_duplicate_source_records(db)
                        await db.execute("INSERT INTO personal_schema_version(version, applied_at) VALUES(4, strftime('%s','now') * 1000)")
                    await _validate_shape(db)
                    async with db.execute("PRAGMA foreign_key_check") as cur:
                        violations = await cur.fetchall()
                    if violations:
                        raise RuntimeError(f"Personal Vault foreign_key_check failed: {violations!r}")
                    await db.commit()
                    yield db
            finally:
                await asyncio.to_thread(fcntl.flock, lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()

    async def delete_vault(self, user_id: int) -> bool:
        """Delete every personal artifact and remove the physical Vault file."""
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        path = self._path(user_id)
        lock = _LOCKS.setdefault(user_id, asyncio.Lock())
        async with lock:
            lock_path = Path(str(path) + ".lock")
            lock_handle = await asyncio.to_thread(lock_path.open, "a+")
            await asyncio.to_thread(fcntl.flock, lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                if not path.exists():
                    return False
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
                    await db.execute("DELETE FROM personal_deletion_audit_events")
                    await db.execute("DELETE FROM personal_migration_conflicts")
                    await db.commit()
                    await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    for suffix in ("", "-wal", "-shm"):
                        candidate = Path(str(path) + suffix)
                        try:
                            candidate.unlink()
                        except FileNotFoundError:
                            pass
            finally:
                await asyncio.to_thread(fcntl.flock, lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
            return True


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


async def _merge_duplicate_source_records(db: aiosqlite.Connection) -> None:
    async with db.execute(
        """SELECT user_id,source_type,source_id,kind,COUNT(*) FROM personal_records
           WHERE source_id<>'' GROUP BY user_id,source_type,source_id,kind HAVING COUNT(*)>1"""
    ) as cur:
        groups = await cur.fetchall()
    for user_id, source_type, source_id, kind, _ in groups:
        async with db.execute(
            """SELECT record_id FROM personal_records
               WHERE user_id=? AND source_type=? AND source_id=? AND kind=?
               ORDER BY created_at,record_id""",
            (user_id, source_type, source_id, kind),
        ) as cur:
            ids = [str(row[0]) for row in await cur.fetchall()]
        await _merge_records(db, ids)


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
    winner_content = safe_memory_text(winner[1])
    for row in rows:
        conflicting_id = str(row[0])
        if conflicting_id == canonical:
            continue
        if safe_memory_text(row[1]) != winner_content:
            conflict_id = "conflict:" + hashlib.sha256(f"{canonical}:{conflicting_id}".encode()).hexdigest()[:24]
            await db.execute(
                """INSERT OR REPLACE INTO personal_migration_conflicts
                   (conflict_id,user_id,source_type,source_id,kind,canonical_record_id,
                    conflicting_record_id,content,authority,explicit,confidence,valid_from,created_at)
                   SELECT ?,user_id,source_type,source_id,kind,?,?,?, ?,?,?,?,? FROM personal_records WHERE record_id=?""",
                (conflict_id, canonical, conflicting_id, safe_memory_text(row[1]), str(row[2]), int(row[6]), float(row[5]), int(row[7]), int(row[9]), conflicting_id),
            )
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


async def _validate_shape(db: aiosqlite.Connection) -> None:
    for table, required in _REQUIRED_COLUMNS.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            actual = {str(row[1]) for row in await cur.fetchall()}
        missing = required - actual
        if missing:
            raise RuntimeError(f"Personal Vault table {table} is missing columns: {sorted(missing)}")
