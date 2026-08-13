"""Personal Vault storage owned by canonical Memory infrastructure."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
import fcntl


_LOCKS: dict[int, asyncio.Lock] = {}
PERSONAL_SCHEMA_VERSION = 2

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
    """CREATE TABLE IF NOT EXISTS personal_access_control_actions (
       rule_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,subject_type TEXT NOT NULL,
       subject_id TEXT NOT NULL,object_type TEXT NOT NULL,object_id TEXT NOT NULL,action TEXT NOT NULL,
       effect TEXT NOT NULL,created_at INTEGER NOT NULL,
       UNIQUE(user_id,subject_type,subject_id,object_type,object_id,action))""",
    """CREATE TABLE IF NOT EXISTS habit_evidence (
       id INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,source_key TEXT NOT NULL,
       context_kind TEXT NOT NULL,polarity TEXT NOT NULL,observed_at INTEGER NOT NULL,
       UNIQUE(record_id,source_key),
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
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=15000")
                    await db.execute("PRAGMA foreign_keys=ON")
                    for statement in _DDL:
                        await db.execute(statement)
                    async with db.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM personal_schema_version"
                    ) as cursor:
                        current = int((await cursor.fetchone())[0])
                    if current < 1:
                        await db.execute("INSERT INTO personal_schema_version(version, applied_at) VALUES(1, strftime('%s','now') * 1000)")
                    projection_fk = await _has_cascade_fk(db, "personal_projections", "personal_records")
                    habit_fk = await _has_cascade_fk(db, "habit_evidence", "personal_records")
                    if current < 2 or not projection_fk or not habit_fk:
                        await db.execute("DELETE FROM personal_projections WHERE record_id NOT IN (SELECT record_id FROM personal_records)")
                        await db.execute("DELETE FROM personal_memory_usage_events WHERE record_id NOT IN (SELECT record_id FROM personal_records)")
                        await db.execute("DELETE FROM habit_evidence WHERE record_id NOT IN (SELECT record_id FROM personal_records)")
                        await _rebuild_fk_tables(db)
                        await db.execute("CREATE INDEX IF NOT EXISTS idx_personal_usage_record ON personal_memory_usage_events(user_id,record_id,used_at)")
                        await db.execute("CREATE INDEX IF NOT EXISTS idx_personal_acl_audit_user ON personal_acl_audit_events(user_id,created_at)")
                        if current < 2:
                            await db.execute("INSERT INTO personal_schema_version(version, applied_at) VALUES(2, strftime('%s','now') * 1000)")
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
            if not path.exists():
                return False
            lock_path = Path(str(path) + ".lock")
            lock_handle = await asyncio.to_thread(lock_path.open, "a+")
            await asyncio.to_thread(fcntl.flock, lock_handle.fileno(), fcntl.LOCK_EX)
            try:
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
                    await db.commit()
            finally:
                await asyncio.to_thread(fcntl.flock, lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(path) + suffix)
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
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
    await db.execute("INSERT INTO habit_evidence SELECT id,record_id,source_key,context_kind,polarity,observed_at FROM habit_evidence_v1")
    await db.execute("DROP TABLE habit_evidence_v1")
    async with db.execute("PRAGMA foreign_key_check") as cur:
        violations = await cur.fetchall()
    if violations:
        raise RuntimeError(f"Personal Vault foreign_key_check failed: {violations!r}")


async def _has_cascade_fk(db: aiosqlite.Connection, table: str, target: str) -> bool:
    async with db.execute(f"PRAGMA foreign_key_list({table})") as cur:
        rows = await cur.fetchall()
    return any(str(row[2]) == target and str(row[6]).upper() == "CASCADE" for row in rows)
