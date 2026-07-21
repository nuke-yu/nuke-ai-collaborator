import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import aiosqlite

from ai.personal_vault import (add_record,connect,delete_vault,export_vault,format_projected_context,
                               ingest_knowledge,observe_habit,project,projected_context,rebuild_vault)
from runtime.dbpaths import personal_db_path


class PersonalVaultTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root_patch=patch("skills.constants.WORKSPACE_ROOT",self.tmp.name)
        self.root_patch.start()

    async def asyncTearDown(self):
        self.root_patch.stop(); self.tmp.cleanup()

    async def test_personal_db_is_outside_group_storage(self):
        record_id=await add_record(user_id=4,kind="expertise",content="I know distributed systems",
                                   source_type="chat",source_id="m1",explicit=True,authority="user_statement")
        self.assertTrue(Path(personal_db_path(4)).exists())
        self.assertIn("_personal/user_4",personal_db_path(4))
        self.assertNotIn("group_",personal_db_path(4))
        async with connect(4) as db:
            async with db.execute("SELECT status FROM personal_records WHERE record_id=?",(record_id,)) as cur:
                self.assertEqual((await cur.fetchone())[0],"active")

    async def test_connection_enables_durable_concurrency_pragmas(self):
        async with connect(4) as db:
            async with db.execute("PRAGMA journal_mode") as cur:
                self.assertEqual((await cur.fetchone())[0].lower(),"wal")

    async def test_v1_schema_migrates_foreign_keys_and_removes_orphans(self):
        path=Path(personal_db_path(4));path.parent.mkdir(parents=True,exist_ok=True)
        async with aiosqlite.connect(path) as db:
            await db.executescript("""
              CREATE TABLE personal_records (
               record_id TEXT PRIMARY KEY,user_id INTEGER,kind TEXT,status TEXT);
              CREATE TABLE personal_projections (
               projection_id TEXT PRIMARY KEY,record_id TEXT NOT NULL,group_id INTEGER NOT NULL,bot_id INTEGER,
               purpose TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',expires_at INTEGER,
               created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(record_id,group_id,bot_id,purpose));
              CREATE TABLE habit_evidence (
               id INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,source_key TEXT NOT NULL,
               context_kind TEXT NOT NULL,polarity TEXT NOT NULL,observed_at INTEGER NOT NULL,
               UNIQUE(record_id,source_key));
              CREATE TABLE _schema_version(version INTEGER NOT NULL,applied_at INTEGER NOT NULL);
              INSERT INTO _schema_version VALUES(1,1);
              INSERT INTO personal_records VALUES('kept',4,'habit','active');
              INSERT INTO personal_projections VALUES('p1','kept',1,NULL,'test','active',NULL,1,1);
              INSERT INTO personal_projections VALUES('orphan','missing',1,NULL,'test','active',NULL,1,1);
              INSERT INTO habit_evidence(record_id,source_key,context_kind,polarity,observed_at)
               VALUES('kept','s1','coding','support',1),('missing','s2','coding','support',1);
            """)
            await db.commit()
        async with connect(4) as db:
            async with db.execute("SELECT MAX(version) FROM _schema_version") as cur:
                self.assertEqual((await cur.fetchone())[0],2)
            async with db.execute("SELECT COUNT(*) FROM personal_projections") as cur:
                self.assertEqual((await cur.fetchone())[0],1)
            async with db.execute("SELECT COUNT(*) FROM habit_evidence") as cur:
                self.assertEqual((await cur.fetchone())[0],1)
            async with db.execute("PRAGMA foreign_key_list(personal_projections)") as cur:
                projection_fk=await cur.fetchone()
            async with db.execute("PRAGMA foreign_key_list(habit_evidence)") as cur:
                evidence_fk=await cur.fetchone()
            self.assertEqual(projection_fk[2:7],("personal_records","record_id","record_id","NO ACTION","CASCADE"))
            self.assertEqual(evidence_fk[2:7],("personal_records","record_id","record_id","NO ACTION","CASCADE"))
            await db.execute("DELETE FROM personal_records WHERE record_id='kept'")
            await db.commit()
            async with db.execute("SELECT COUNT(*) FROM personal_projections") as cur:
                self.assertEqual((await cur.fetchone())[0],0)
            async with db.execute("SELECT COUNT(*) FROM habit_evidence") as cur:
                self.assertEqual((await cur.fetchone())[0],0)
            async with db.execute("PRAGMA busy_timeout") as cur:
                self.assertEqual((await cur.fetchone())[0],5000)
            async with db.execute("PRAGMA foreign_keys") as cur:
                self.assertEqual((await cur.fetchone())[0],1)
        async with aiosqlite.connect(personal_db_path(4)) as db:
            async with db.execute("PRAGMA journal_mode") as cur:
                self.assertEqual((await cur.fetchone())[0].lower(),"wal")

    async def test_projection_is_group_bot_and_purpose_scoped(self):
        record_id=await add_record(user_id=4,kind="preference",content="Use concise reports",
                                   source_type="chat",explicit=True,authority="user_statement")
        await project(user_id=4,record_id=record_id,group_id=10,bot_id=2,purpose="assistant_context")
        self.assertEqual(len(await projected_context(user_id=4,group_id=10,bot_id=2,purpose="assistant_context")),1)
        self.assertEqual(await projected_context(user_id=4,group_id=11,bot_id=2,purpose="assistant_context"),[])
        self.assertEqual(await projected_context(user_id=4,group_id=10,bot_id=3,purpose="assistant_context"),[])

    async def test_secret_cannot_be_projected_and_observation_is_provisional(self):
        record_id=await add_record(user_id=4,kind="workflow",content="token AKIAIOSFODNN7EXAMPLE",
                                   source_type="email",sensitivity="secret")
        with self.assertRaises(ValueError):
            await project(user_id=4,record_id=record_id,group_id=1,bot_id=None,purpose="assistant_context")
        async with connect(4) as db:
            async with db.execute("SELECT content,status FROM personal_records WHERE record_id=?",(record_id,)) as cur:
                content,status=await cur.fetchone()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE",content); self.assertEqual(status,"provisional")

    async def test_statement_attribution_does_not_turn_third_party_into_user_view(self):
        own=await ingest_knowledge(user_id=4,kind="decision",statement="I prefer written decisions",
                                   source_type="chat",source_id="1",speaker="me",subject="4",
                                   context_kind="planning",asserted_by_user=True)
        other=await ingest_knowledge(user_id=4,kind="decision",statement="Alice dislikes reviews",
                                     source_type="email",source_id="2",speaker="Alice",subject="Alice",
                                     context_kind="email",asserted_by_user=False)
        async with connect(4) as db:
            async with db.execute("SELECT record_id,authority,status FROM personal_records ORDER BY record_id") as cur:
                rows={r[0]:(r[1],r[2]) for r in await cur.fetchall()}
        self.assertEqual(rows[own],("user_statement","active"))
        self.assertEqual(rows[other],("third_party","provisional"))

    async def test_explicit_statement_upgrades_matching_observation_without_later_downgrade(self):
        record_id=await add_record(user_id=4,kind="preference",content="Prefer written plans",
                                   source_type="chat",source_id="message-1",speaker="assistant",
                                   subject="4",authority="observed",sensitivity="restricted",
                                   confidence=.45,explicit=False)
        confirmed_id=await add_record(user_id=4,kind="preference",content="Prefer written plans",
                                      source_type="chat",source_id="message-1",speaker="me",
                                      subject="4",authority="user_statement",sensitivity="private",
                                      confidence=1.0,explicit=True)
        self.assertEqual(confirmed_id,record_id)
        await add_record(user_id=4,kind="preference",content="Prefer written plans",
                         source_type="chat",source_id="message-1",speaker="assistant",
                         subject="4",authority="observed",sensitivity="private",
                         confidence=.2,explicit=False)
        await add_record(user_id=4,kind="preference",content="Prefer written plans",
                         source_type="chat",source_id="message-1",speaker="forged",
                         subject="attacker",authority="user_statement",sensitivity="private",
                         confidence=1.0,explicit=True)
        async with connect(4) as db:
            async with db.execute("SELECT speaker,authority,sensitivity,status,confidence,explicit "
                                  "FROM personal_records WHERE record_id=?",(record_id,)) as cur:
                row=await cur.fetchone()
        self.assertEqual(row,("me","user_statement","restricted","active",1.0,1))

    async def test_record_authority_and_explicitness_must_be_consistent(self):
        with self.assertRaisesRegex(ValueError,"invalid authority"):
            await add_record(user_id=4,kind="profile",content="invalid",source_type="manual",
                             authority="model_guess",explicit=False)
        with self.assertRaisesRegex(ValueError,"require user_statement"):
            await add_record(user_id=4,kind="profile",content="invalid",source_type="manual",
                             authority="observed",explicit=True)
        with self.assertRaisesRegex(ValueError,"require user_statement"):
            await add_record(user_id=4,kind="profile",content="invalid",source_type="manual",
                             authority="user_statement",explicit=False)

    async def test_habit_requires_samples_contexts_time_and_no_counterexample(self):
        day=86_400_000
        for source,context,when in (("1","coding",0),("2","review",7*day),("3","coding",15*day)):
            record_id=await observe_habit(user_id=4,habit_key="concise",statement="Prefers concise output",
                                          source_type="task",source_id=source,context_kind=context,
                                          observed_at=when,polarity="support")
        async with connect(4) as db:
            async with db.execute("SELECT status FROM personal_records WHERE record_id=?",(record_id,)) as cur:
                self.assertEqual((await cur.fetchone())[0],"active")
        await observe_habit(user_id=4,habit_key="concise",statement="Prefers concise output",
                            source_type="task",source_id="4",context_kind="incident",observed_at=16*day,
                            polarity="contradict")
        async with connect(4) as db:
            async with db.execute("SELECT status FROM personal_records WHERE record_id=?",(record_id,)) as cur:
                self.assertEqual((await cur.fetchone())[0],"provisional")

    async def test_context_export_rebuild_and_delete(self):
        record_id=await add_record(user_id=4,kind="expertise",content="Knows Python",source_type="manual",
                                   authority="user_statement",explicit=True)
        await project(user_id=4,record_id=record_id,group_id=2,bot_id=None,purpose="assistant_context",expires_at=1)
        result=await rebuild_vault(4); self.assertEqual(result["expired_projections"],1)
        await project(user_id=4,record_id=record_id,group_id=2,bot_id=None,purpose="assistant_context")
        context=await format_projected_context(user_id=4,group_id=2,bot_id=9)
        self.assertIn("Knows Python",context)
        exported=await export_vault(4); self.assertEqual(exported["user_id"],4); self.assertEqual(len(exported["records"]),1)
        self.assertTrue(await delete_vault(4)); self.assertFalse(Path(personal_db_path(4)).exists())
