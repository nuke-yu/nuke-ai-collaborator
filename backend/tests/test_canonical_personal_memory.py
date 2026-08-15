from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

from memory.application import CanonicalPersonalKnowledgeService
from memory.contracts import (
    CreatePersonalProjection,
    CreatePersonalRecord,
    FormatProjectedContext,
    IngestPersonalKnowledge,
    ObservePersonalHabit,
)
from memory.domain import MemoryScope
from memory.infrastructure import PersonalVaultDatabase


class _TempPersonalDatabase(PersonalVaultDatabase):
    def __init__(self, path: str) -> None:
        self.path = path

    def _path(self, user_id: int) -> Path:
        return Path(self.path)

    @asynccontextmanager
    async def connect(self, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            for statement in self.__class__._ddl_for_test():
                await db.execute(statement)
            await db.commit()
            yield db

    @staticmethod
    def _ddl_for_test():
        # The production database owns the schema; this subclass only routes
        # the same service into an isolated test file.
        from memory.infrastructure.personal_database import _DDL
        return _DDL


class _ProductionTempPersonalDatabase(PersonalVaultDatabase):
    def __init__(self, path: str) -> None:
        self.path = path

    def _path(self, user_id: int) -> Path:
        return Path(self.path)


class CanonicalPersonalMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_personal.db")
        self.database = _TempPersonalDatabase(self.path)
        self.service = CanonicalPersonalKnowledgeService(self.database)
        self.scope = MemoryScope.personal(user_id=7, actor_id="user:7", group_id=9)

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_record_projection_and_context_stay_in_canonical_vault(self) -> None:
        record_id = await self.service.create_record(CreatePersonalRecord(
            scope=self.scope, kind="preference", content="I prefer dark mode",
            source_type="manual", sensitivity="private",
        ))
        async with self.database.connect(7) as db:
            await db.execute("INSERT INTO personal_apps(app_id,user_id,name,status,created_at,updated_at) VALUES('chat',7,'Chat','active',1,1)")
            await db.commit()
        await self.service.create_projection(CreatePersonalProjection(
            scope=self.scope, record_id=record_id, target_group_id=9,
            purpose="assistant_context", app_id="chat",
        ))
        context = await self.service.format_projected_context(FormatProjectedContext(
            scope=self.scope, purpose="assistant_context", app_id="chat",
        ))
        self.assertIn("dark mode", context)

    async def test_secret_personal_record_cannot_be_projected(self) -> None:
        record_id = await self.service.create_record(CreatePersonalRecord(
            scope=self.scope, kind="profile", content="secret value",
            sensitivity="secret",
        ))
        with self.assertRaisesRegex(ValueError, "cannot be projected"):
            await self.service.create_projection(CreatePersonalProjection(
                scope=self.scope, record_id=record_id, target_group_id=9,
            ))

    async def test_sensitivity_only_escalates_and_revokes_existing_projection(self) -> None:
        command = CreatePersonalRecord(
            scope=self.scope, kind="preference", content="sensitive preference",
            source_type="manual", source_id="same", sensitivity="private",
        )
        record_id = await self.service.create_record(command)
        await self.service.create_projection(CreatePersonalProjection(
            scope=self.scope, record_id=record_id, target_group_id=9,
        ))
        await self.service.create_record(CreatePersonalRecord(
            scope=self.scope, kind="preference", content="sensitive preference",
            source_type="manual", source_id="same", sensitivity="secret",
        ))
        async with self.database.connect(7) as db:
            async with db.execute("SELECT sensitivity FROM personal_records WHERE record_id=?", (record_id,)) as cur:
                self.assertEqual((await cur.fetchone())[0], "secret")
            async with db.execute("SELECT status FROM personal_projections WHERE record_id=?", (record_id,)) as cur:
                self.assertEqual((await cur.fetchone())[0], "revoked")
        await self.service.create_record(command)
        async with self.database.connect(7) as db:
            async with db.execute("SELECT sensitivity FROM personal_records WHERE record_id=?", (record_id,)) as cur:
                self.assertEqual((await cur.fetchone())[0], "secret")

    async def test_source_identity_does_not_overwrite_distinct_facts(self) -> None:
        first = await self.service.ingest(IngestPersonalKnowledge(
            scope=self.scope, kind="preference", statement="old value",
            source_type="profile", source_id="profile:7", sensitivity="private",
        ))
        await self.service.create_projection(CreatePersonalProjection(
            scope=self.scope, record_id=first, target_group_id=9,
        ))
        second = await self.service.ingest(IngestPersonalKnowledge(
            scope=self.scope, kind="preference", statement="Authorization: Bearer AAAAAAAAAAAAAAAAAAAA",
            source_type="profile", source_id="profile:7", sensitivity="secret",
        ))
        self.assertNotEqual(first, second)
        async with self.database.connect(7) as db:
            async with db.execute("SELECT content,sensitivity FROM personal_records WHERE record_id=?", (first,)) as cur:
                self.assertEqual((await cur.fetchone()), ("old value", "private"))
            async with db.execute("SELECT content,sensitivity FROM personal_records WHERE record_id=?", (second,)) as cur:
                self.assertEqual((await cur.fetchone())[1], "secret")
            async with db.execute("SELECT status FROM personal_projections WHERE record_id=?", (first,)) as cur:
                self.assertEqual((await cur.fetchone())[0], "active")

    async def test_future_personal_schema_is_rejected_without_creating_tables(self) -> None:
        path = tempfile.mktemp(suffix="_future_personal.db")
        try:
            async with aiosqlite.connect(path) as db:
                await db.execute("CREATE TABLE personal_schema_version(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)")
                await db.execute("INSERT INTO personal_schema_version VALUES(999, 1)")
                await db.commit()
            with self.assertRaisesRegex(RuntimeError, "newer than supported"):
                await _ProductionTempPersonalDatabase(path).connect(7).__aenter__()
            async with aiosqlite.connect(path) as db:
                async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='personal_records'") as cur:
                    self.assertIsNone(await cur.fetchone())
                async with db.execute("PRAGMA journal_mode") as cur:
                    self.assertEqual((await cur.fetchone())[0].lower(), "delete")
        finally:
            for suffix in ("", "-wal", "-shm", ".lock"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass

    async def test_v3_duplicate_migration_preserves_authoritative_record_and_conflict(self) -> None:
        path = tempfile.mktemp(suffix="_duplicate_personal.db")
        try:
            from memory.infrastructure.personal_database import _DDL
            async with aiosqlite.connect(path) as db:
                for statement in _DDL:
                    await db.execute(statement)
                await db.execute("DROP TABLE personal_migration_conflicts")
                await db.execute("""CREATE TABLE personal_migration_conflicts(
                    conflict_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,kind TEXT NOT NULL,canonical_record_id TEXT NOT NULL,
                    conflicting_record_id TEXT NOT NULL,content TEXT NOT NULL,authority TEXT NOT NULL,
                    explicit INTEGER NOT NULL,confidence REAL NOT NULL,valid_from INTEGER NOT NULL,created_at INTEGER NOT NULL)""")
                await db.executemany("INSERT INTO personal_schema_version(version,applied_at) VALUES(?,1)", [(1,), (2,), (3,)])
                rows = [
                    ("r-old", 7, "preference", "same value", "", "7", "observed", "private", "active", "profile", "profile:7", .4, 0, 10, 10, 10),
                    ("r-new", 7, "preference", "same value", "", "7", "user_statement", "restricted", "active", "profile", "profile:7", .9, 1, 20, 20, 20),
                    ("r-other", 7, "preference", "new statement", "", "7", "observed", "private", "active", "profile", "profile:7", .6, 0, 30, 30, 30),
                ]
                await db.executemany("""INSERT INTO personal_records
                    (record_id,user_id,kind,content,speaker,subject,authority,sensitivity,status,source_type,source_id,confidence,explicit,valid_from,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
                await db.commit()
            async with _ProductionTempPersonalDatabase(path).connect(7) as db:
                async with db.execute("SELECT record_id,content,authority,sensitivity,explicit,confidence FROM personal_records") as cur:
                    self.assertEqual(await cur.fetchall(), [
                        ("r-new", "same value", "user_statement", "restricted", 1, .9),
                        ("r-other", "new statement", "observed", "private", 0, .6),
                    ])
                async with db.execute("SELECT conflicting_record_id,content FROM personal_migration_conflicts") as cur:
                    self.assertEqual(await cur.fetchall(), [("r-other", "new statement")])
        finally:
            for suffix in ("", "-wal", "-shm", ".lock"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass

    async def test_impact_reports_usage_and_delete_removes_entire_vault(self) -> None:
        record_id = await self.service.create_record(CreatePersonalRecord(
            scope=self.scope, kind="preference", content="dark mode",
            source_type="manual", sensitivity="private",
        ))
        async with self.database.connect(7) as db:
            await db.execute(
                "INSERT INTO personal_projections(projection_id,record_id,group_id,bot_id,purpose,created_at,updated_at) VALUES('p:1',?,?,?,?,1,1)",
                (record_id, 9, 5, "assistant_context"),
            )
            await db.execute(
                "INSERT INTO personal_memory_usage_events(user_id,record_id,projection_id,group_id,bot_id,session_id,purpose,used_at) VALUES(?,?,?,?,?,?,?,?)",
                (7, record_id, "p:1", 9, 5, "run:1", "assistant_context", 2),
            )
            await db.execute("INSERT INTO personal_apps(app_id,user_id,name,created_at,updated_at) VALUES('app:1',7,'App',1,1)")
            await db.execute("INSERT INTO personal_access_control_actions(user_id,subject_type,subject_id,object_type,object_id,action,effect,created_at) VALUES(7,'user','7','personal','7','read','deny',1)")
            await db.commit()
        impact = await self.service.get_record_impact(self.scope, record_id)
        self.assertEqual(impact["affected_session_ids"], ["run:1"])
        self.assertEqual(len(impact["usage_events"]), 1)
        deletion = await self.service.delete(self.scope)
        self.assertEqual(deletion, {"deleted": True, "audit_pending": False, "audit_status": "completed"})
        self.assertFalse(Path(self.path).exists())

    async def test_delete_returns_success_when_central_audit_needs_retry(self) -> None:
        path = tempfile.mktemp(suffix="_central_audit_pending.db")
        try:
            from memory.infrastructure.personal_database import _DDL
            async with aiosqlite.connect(path) as db:
                for statement in _DDL:
                    await db.execute(statement)
                await db.commit()
            database = PersonalVaultDatabase()
            with patch.object(PersonalVaultDatabase, "_path", staticmethod(lambda _user_id: Path(path))), \
                 patch("memory.infrastructure.personal_database._create_central_vault_deletion", new=AsyncMock(return_value=(17, "op-17"))), \
                 patch("memory.infrastructure.personal_database._finish_central_vault_deletion", new=AsyncMock(return_value=False)):
                result = await database.delete_vault(7)
            self.assertEqual(result, {"deleted": True, "audit_pending": True, "audit_status": "pending"})
            self.assertFalse(Path(path).exists())
        finally:
            for suffix in ("", "-wal", "-shm", ".lock"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass

    async def test_delete_releases_lock_when_central_audit_creation_fails(self) -> None:
        path = tempfile.mktemp(suffix="_central_audit_unavailable.db")
        try:
            from memory.infrastructure.personal_database import _DDL
            async with aiosqlite.connect(path) as db:
                for statement in _DDL:
                    await db.execute(statement)
                await db.commit()
            database = PersonalVaultDatabase()
            with patch.object(PersonalVaultDatabase, "_path", staticmethod(lambda _user_id: Path(path))), \
                 patch("memory.infrastructure.personal_database._create_central_vault_deletion", new=AsyncMock(side_effect=RuntimeError("central down"))):
                result = await database.delete_vault(7)
                self.assertEqual(result, {"deleted": False, "audit_pending": False, "audit_status": "unavailable"})
                self.assertEqual(await database.delete_vault(7), {"deleted": False, "audit_pending": False, "audit_status": "unavailable"})
        finally:
            for suffix in ("", "-wal", "-shm", ".lock"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass

    async def test_missing_central_audit_schema_fails_readiness_check(self) -> None:
        path = tempfile.mktemp(suffix="_central_without_audit.db")
        try:
            import db
            from memory.infrastructure.personal_database import _create_central_vault_deletion
            with patch.object(db, "DB_PATH", path):
                with self.assertRaisesRegex(RuntimeError, "schema is not ready"):
                    await _create_central_vault_deletion(7)
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass

    async def test_pending_sweeper_requires_commit_marker_and_missing_vault(self) -> None:
        central = tempfile.mktemp(suffix="_central_sweeper.db")
        vault = Path(tempfile.mktemp(suffix="_vault.db"))
        marker = Path(f"{vault}.delete.op-1.committed")
        try:
            import db
            from memory.infrastructure.personal_database import sweep_pending_vault_deletions
            await db.init_central_db(central)
            marker.write_text("op-1", encoding="utf-8")
            async with aiosqlite.connect(central) as conn:
                await conn.execute(
                    "INSERT INTO personal_vault_deletion_audit(operation_id,user_id,operation,status,created_at,local_commit_marker) VALUES(?,?,?,?,?,?)",
                    ("op-1", 7, "delete_vault", "pending", 1, str(marker)),
                )
                await conn.commit()
            with patch.object(db, "DB_PATH", central), \
                 patch.object(PersonalVaultDatabase, "_path", staticmethod(lambda _user_id: vault)):
                result = await sweep_pending_vault_deletions()
            self.assertEqual(result, {"scanned": 1, "completed": 1, "skipped": 0})
            self.assertFalse(marker.exists())
        finally:
            for path in (central, vault):
                for suffix in ("", "-wal", "-shm"):
                    try:
                        os.unlink(str(path) + suffix)
                    except FileNotFoundError:
                        pass

    async def test_habit_uses_stable_key_and_matures_from_evidence(self) -> None:
        base = 1_700_000_000_000
        ids = []
        for index, (context, offset) in enumerate((("home", 0), ("office", 7), ("home", 15))):
            ids.append(await self.service.observe_habit(ObservePersonalHabit(
                scope=self.scope, habit_key="daily-review", statement="review tasks",
                source_type="observation", source_id=f"event:{index}", context_kind=context,
                observed_at=base + offset * 86_400_000,
            )))
        self.assertEqual(len(set(ids)), 1)
        exported = await self.service.export(self.scope)
        habit = next(item for item in exported["records"] if item["record_id"] == ids[0])
        self.assertEqual(habit["status"], "active")
        self.assertEqual(len(exported["habit_evidence"]), 3)

    async def test_habit_source_type_prevents_cross_system_evidence_overwrite(self) -> None:
        base = 1_700_000_000_000
        for source_type in ("calendar", "chat"):
            await self.service.observe_habit(ObservePersonalHabit(
                scope=self.scope, habit_key="daily-review", statement="review tasks",
                source_type=source_type, source_id="event-1", context_kind=source_type,
                observed_at=base,
            ))
        exported = await self.service.export(self.scope)
        self.assertEqual(len(exported["habit_evidence"]), 2)
        self.assertEqual({row["source_type"] for row in exported["habit_evidence"]}, {"calendar", "chat"})

    async def test_export_cursor_reports_and_retrieves_remaining_collections(self) -> None:
        for index in range(3):
            await self.service.create_record(CreatePersonalRecord(
                scope=self.scope, kind="preference", content=f"value-{index}",
                source_type="test", source_id=f"test:{index}",
            ))
        first = await self.service.export(self.scope, limit=2)
        self.assertEqual(len(first["records"]), 2)
        self.assertIsInstance(first["export"]["next_cursor"], str)
        self.assertTrue(first["export"]["has_more"]["records"])
        with self.assertRaises(ValueError):
            await self.service.export(self.scope, cursor=first["export"]["next_cursor"][:-1] + "x", limit=2)
        with self.assertRaises(ValueError):
            await self.service.export(self.scope, cursor=first["export"]["next_cursor"], limit=1)
        second = await self.service.export(self.scope, cursor=first["export"]["next_cursor"], limit=2)
        self.assertEqual(len(second["records"]), 1)
        self.assertIsNone(second["export"]["next_cursor"])

    async def test_record_and_projection_deletion_write_audit_without_content(self) -> None:
        record_id = await self.service.create_record(CreatePersonalRecord(
            scope=self.scope, kind="preference", content="delete me", source_type="manual", source_id="delete:1",
        ))
        projection_id = await self.service.create_projection(CreatePersonalProjection(
            scope=self.scope, record_id=record_id, target_group_id=9,
        ))
        self.assertTrue(await self.service.revoke_projection(self.scope, projection_id))
        self.assertTrue(await self.service.delete_record(self.scope, record_id))
        exported = await self.service.export(self.scope)
        self.assertEqual({row["operation"] for row in exported["deletion_audit_events"]}, {"revoke_projection", "delete_record"})
        self.assertTrue(all("content" not in row for row in exported["deletion_audit_events"]))

    async def test_mislabeled_v2_vault_is_repaired_by_shape_validation(self) -> None:
        path = tempfile.mktemp(suffix="_legacy_personal.db")
        try:
            async with aiosqlite.connect(path) as db:
                await db.executescript("""
                    CREATE TABLE personal_schema_version(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
                    INSERT INTO personal_schema_version VALUES(2, 1);
                    CREATE TABLE personal_records(record_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
                        content TEXT NOT NULL, speaker TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '',
                        authority TEXT NOT NULL, sensitivity TEXT NOT NULL DEFAULT 'private', status TEXT NOT NULL DEFAULT 'active',
                        source_type TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0.5,
                        explicit INTEGER NOT NULL DEFAULT 0, valid_from INTEGER NOT NULL, valid_to INTEGER,
                        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
                    CREATE TABLE personal_projections(projection_id TEXT PRIMARY KEY,record_id TEXT NOT NULL,group_id INTEGER NOT NULL,
                        bot_id INTEGER,purpose TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',expires_at INTEGER,
                        created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
                        UNIQUE(record_id,group_id,bot_id,purpose));
                    CREATE TABLE habit_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,source_key TEXT NOT NULL,
                        context_kind TEXT NOT NULL,polarity TEXT NOT NULL,observed_at INTEGER NOT NULL,UNIQUE(record_id,source_key));
                    INSERT INTO personal_records VALUES('r:1',7,'habit','review','','habit','observed','private','active','observation','s:1',0.45,0,1,NULL,1,1);
                    INSERT INTO personal_projections VALUES('p:1','r:1',9,NULL,'context','active',NULL,1,1);
                    INSERT INTO habit_evidence(record_id,source_key,context_kind,polarity,observed_at)
                        VALUES('r:1','s:1','home','support',1);
                """)
                await db.commit()

            database = _ProductionTempPersonalDatabase(path)
            async with database.connect(7) as db:
                async with db.execute("PRAGMA foreign_key_list(personal_projections)") as cur:
                    projection_fks = await cur.fetchall()
                async with db.execute("PRAGMA foreign_key_list(habit_evidence)") as cur:
                    habit_fks = await cur.fetchall()
                async with db.execute("PRAGMA foreign_key_check") as cur:
                    self.assertEqual(await cur.fetchall(), [])
            self.assertTrue(any(row[2] == "personal_records" and row[6] == "CASCADE" for row in projection_fks))
            self.assertTrue(any(row[2] == "personal_records" and row[6] == "CASCADE" for row in habit_fks))
        finally:
            for suffix in ("", "-wal", "-shm", ".lock"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass
