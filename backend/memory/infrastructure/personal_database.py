"""Personal Vault storage owned by canonical Memory infrastructure."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


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
       UNIQUE(record_id,source_key))""",
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
            async with aiosqlite.connect(str(path), timeout=5.0) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                await db.execute("PRAGMA foreign_keys=ON")
                for statement in _DDL:
                    await db.execute(statement)
                async with db.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM personal_schema_version"
                ) as cursor:
                    current = int((await cursor.fetchone())[0])
                if current < 1:
                    await db.execute(
                        "INSERT INTO personal_schema_version(version, applied_at) VALUES(1, strftime('%s','now') * 1000)"
                    )
                if current < 2:
                    # Older Vaults did not enforce cleanup for orphaned rows.
                    # Repair those rows before recording the upgraded schema.
                    await db.execute(
                        "DELETE FROM personal_projections WHERE record_id NOT IN (SELECT record_id FROM personal_records)"
                    )
                    await db.execute(
                        "DELETE FROM personal_memory_usage_events WHERE record_id NOT IN (SELECT record_id FROM personal_records)"
                    )
                    await db.execute(
                        "DELETE FROM habit_evidence WHERE record_id NOT IN (SELECT record_id FROM personal_records)"
                    )
                    await db.execute(
                        "CREATE INDEX IF NOT EXISTS idx_personal_usage_record ON personal_memory_usage_events(user_id,record_id,used_at)"
                    )
                    await db.execute(
                        "CREATE INDEX IF NOT EXISTS idx_personal_acl_audit_user ON personal_acl_audit_events(user_id,created_at)"
                    )
                    await db.execute(
                        "INSERT INTO personal_schema_version(version, applied_at) VALUES(2, strftime('%s','now') * 1000)"
                    )
                await db.commit()
                yield db

    async def delete_vault(self, user_id: int) -> bool:
        """Delete every personal artifact and remove the physical Vault file."""
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        path = self._path(user_id)
        lock = _LOCKS.setdefault(user_id, asyncio.Lock())
        async with lock:
            if not path.exists():
                return False
            async with aiosqlite.connect(str(path), timeout=5.0) as db:
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute("DELETE FROM personal_projections")
                await db.execute("DELETE FROM personal_memory_usage_events")
                await db.execute(
                    "DELETE FROM habit_evidence WHERE record_id IN (SELECT record_id FROM personal_records)"
                )
                await db.execute("DELETE FROM personal_records")
                await db.execute("DELETE FROM personal_apps")
                await db.execute("DELETE FROM personal_access_control_actions")
                await db.execute("DELETE FROM personal_acl_audit_events")
                await db.commit()
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(path) + suffix)
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
            return True
