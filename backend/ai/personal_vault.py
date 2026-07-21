"""Physically isolated Personal Knowledge Vault and explicit Group projections."""
from __future__ import annotations
from contextlib import asynccontextmanager
import hashlib
import time
import aiosqlite


_DDL = """
CREATE TABLE IF NOT EXISTS personal_records (
 record_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,
 speaker TEXT NOT NULL DEFAULT '',subject TEXT NOT NULL DEFAULT '',authority TEXT NOT NULL,
 sensitivity TEXT NOT NULL DEFAULT 'private',status TEXT NOT NULL DEFAULT 'active',
 source_type TEXT NOT NULL,source_id TEXT NOT NULL DEFAULT '',confidence REAL NOT NULL DEFAULT 0.5,
 explicit INTEGER NOT NULL DEFAULT 0,valid_from INTEGER NOT NULL,valid_to INTEGER,
 created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_personal_records_active ON personal_records(user_id,kind,status);
CREATE TABLE IF NOT EXISTS personal_projections (
 projection_id TEXT PRIMARY KEY,record_id TEXT NOT NULL,group_id INTEGER NOT NULL,bot_id INTEGER,
 purpose TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',expires_at INTEGER,
 created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(record_id,group_id,bot_id,purpose));
CREATE TABLE IF NOT EXISTS personal_sources (
 source_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,source_type TEXT NOT NULL,source_id TEXT NOT NULL,
 speaker TEXT NOT NULL DEFAULT '',subject TEXT NOT NULL DEFAULT '',context_kind TEXT NOT NULL DEFAULT '',
 observed_at INTEGER NOT NULL,content_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS habit_evidence (
 id INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,source_key TEXT NOT NULL,
 context_kind TEXT NOT NULL,polarity TEXT NOT NULL,observed_at INTEGER NOT NULL,
 UNIQUE(record_id,source_key));
CREATE TABLE IF NOT EXISTS _schema_version(version INTEGER NOT NULL,applied_at INTEGER NOT NULL);
"""


@asynccontextmanager
async def connect(user_id:int):
    from runtime.dbpaths import personal_db_path
    path=personal_db_path(user_id)
    from pathlib import Path
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    db=await aiosqlite.connect(path,timeout=5.0)
    try:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")
        async with db.execute("PRAGMA journal_mode") as cur:
            journal_mode=(await cur.fetchone())[0]
        if str(journal_mode).lower()!="wal":
            await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(_DDL)
        await db.execute("INSERT INTO _schema_version(version,applied_at) SELECT 1,? "
                         "WHERE NOT EXISTS(SELECT 1 FROM _schema_version)",(int(time.time()*1000),))
        await db.commit(); yield db
    finally:
        await db.close()


async def add_record(*,user_id:int,kind:str,content:str,source_type:str,source_id:str="",
                     speaker:str="",subject:str="",authority:str="observed",
                     sensitivity:str="private",confidence:float=.5,explicit:bool=False) -> str:
    if kind not in {"profile","expertise","decision","workflow","social","preference","habit","temporary"}:
        raise ValueError("unsupported personal knowledge kind")
    if sensitivity not in {"private","restricted","secret"}:raise ValueError("invalid sensitivity")
    if authority not in {"observed","third_party","user_statement"}:raise ValueError("invalid authority")
    if explicit != (authority=="user_statement"):
        raise ValueError("explicit records require user_statement authority")
    from executors.redaction import redact_secrets
    safe,_=redact_secrets(content)
    now=int(time.time()*1000); key=f"{user_id}:{source_type}:{source_id}:{kind}:{safe}"
    record_id="personal:"+hashlib.sha256(key.encode()).hexdigest()[:24]
    status="active" if explicit else "provisional"
    async with connect(user_id) as db:
        await db.execute("""INSERT INTO personal_records
          (record_id,user_id,kind,content,speaker,subject,authority,sensitivity,status,source_type,
           source_id,confidence,explicit,valid_from,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(record_id) DO UPDATE SET
           speaker=CASE WHEN excluded.explicit=1 AND personal_records.explicit=0
            THEN excluded.speaker ELSE personal_records.speaker END,
           subject=CASE WHEN excluded.explicit=1 AND personal_records.explicit=0
            THEN excluded.subject ELSE personal_records.subject END,
           authority=CASE WHEN excluded.explicit=1 AND personal_records.explicit=0
            THEN excluded.authority ELSE personal_records.authority END,
           sensitivity=CASE
            WHEN personal_records.sensitivity='secret' OR excluded.sensitivity='secret' THEN 'secret'
            WHEN personal_records.sensitivity='restricted' OR excluded.sensitivity='restricted' THEN 'restricted'
            ELSE 'private' END,
           status=CASE WHEN excluded.explicit=1 THEN 'active' ELSE personal_records.status END,
           confidence=MAX(personal_records.confidence,excluded.confidence),
           explicit=MAX(personal_records.explicit,excluded.explicit),
           updated_at=excluded.updated_at""",
          (record_id,user_id,kind,safe,speaker,subject,authority,sensitivity,status,source_type,
           source_id,max(0,min(1,confidence)),int(explicit),now,now,now))
        await db.commit()
    return record_id


async def project(*,user_id:int,record_id:str,group_id:int,bot_id:int|None,purpose:str,
                  expires_at:int|None=None) -> str:
    async with connect(user_id) as db:
        async with db.execute("SELECT sensitivity,status FROM personal_records WHERE record_id=? AND user_id=?",
                              (record_id,user_id)) as cur: row=await cur.fetchone()
        if not row:raise ValueError("personal record not found")
        if row[0]=="secret":raise ValueError("secret personal knowledge cannot be projected")
        if row[1] not in {"active","provisional"}:raise ValueError("inactive record cannot be projected")
        key=f"{record_id}:{group_id}:{bot_id}:{purpose}"
        projection_id="projection:"+hashlib.sha256(key.encode()).hexdigest()[:24]; now=int(time.time()*1000)
        await db.execute("""INSERT INTO personal_projections
          (projection_id,record_id,group_id,bot_id,purpose,expires_at,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(projection_id)
          DO UPDATE SET status='active',expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
          (projection_id,record_id,group_id,bot_id,purpose,expires_at,now,now)); await db.commit()
    return projection_id


async def projected_context(*,user_id:int,group_id:int,bot_id:int|None,purpose:str,limit:int=20)->list[dict]:
    now=int(time.time()*1000)
    async with connect(user_id) as db:
        async with db.execute("""SELECT r.record_id,r.kind,r.content,r.authority,r.confidence
          FROM personal_projections p JOIN personal_records r ON r.record_id=p.record_id
          WHERE r.user_id=? AND p.group_id=? AND (p.bot_id IS NULL OR p.bot_id=?) AND p.purpose=?
          AND p.status='active' AND r.status IN ('active','provisional')
          AND (p.expires_at IS NULL OR p.expires_at>?) ORDER BY r.explicit DESC,r.confidence DESC LIMIT ?""",
          (user_id,group_id,bot_id,purpose,now,max(1,min(limit,100)))) as cur: rows=await cur.fetchall()
    return [{"record_id":r[0],"kind":r[1],"content":r[2],"authority":r[3],"confidence":r[4]} for r in rows]


async def ingest_knowledge(*,user_id:int,kind:str,statement:str,source_type:str,source_id:str,
                           speaker:str,subject:str,context_kind:str,observed_at:int|None=None,
                           asserted_by_user:bool=False,sensitivity:str="private") -> str:
    """Ingest an extracted statement, never an email/chat credential or raw mailbox dump."""
    observed=observed_at or int(time.time()*1000)
    authority="user_statement" if asserted_by_user and str(subject)==str(user_id) else (
        "third_party" if str(subject)!=str(user_id) else "observed")
    record_id=await add_record(user_id=user_id,kind=kind,content=statement,source_type=source_type,
                               source_id=source_id,speaker=speaker,subject=subject,authority=authority,
                               sensitivity=sensitivity,confidence=1.0 if authority=="user_statement" else .45,
                               explicit=authority=="user_statement")
    source_key=f"{source_type}:{source_id}"
    async with connect(user_id) as db:
        await db.execute("""INSERT INTO personal_sources
          (source_key,user_id,source_type,source_id,speaker,subject,context_kind,observed_at,content_hash,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO NOTHING""",
          (source_key,user_id,source_type,source_id,speaker,subject,context_kind,observed,
           hashlib.sha256(statement.encode()).hexdigest(),int(time.time()*1000))); await db.commit()
    return record_id


async def observe_habit(*,user_id:int,habit_key:str,statement:str,source_type:str,source_id:str,
                        context_kind:str,observed_at:int,polarity:str="support") -> str:
    if polarity not in {"support","contradict"}:raise ValueError("invalid habit evidence polarity")
    record_id="habit:"+hashlib.sha256(f"{user_id}:{habit_key}".encode()).hexdigest()[:24]
    now=int(time.time()*1000); source_key=f"{source_type}:{source_id}"
    async with connect(user_id) as db:
        await db.execute("""INSERT INTO personal_records
          (record_id,user_id,kind,content,authority,sensitivity,status,source_type,source_id,
           confidence,explicit,valid_from,created_at,updated_at) VALUES(?,?,'habit',?,'observed','private',
           'provisional',?,?,.35,0,?,?,?) ON CONFLICT(record_id) DO UPDATE SET updated_at=excluded.updated_at""",
          (record_id,user_id,statement,source_type,source_id,observed_at,now,now))
        await db.execute("""INSERT INTO habit_evidence(record_id,source_key,context_kind,polarity,observed_at)
          VALUES(?,?,?,?,?) ON CONFLICT(record_id,source_key) DO UPDATE SET polarity=excluded.polarity,
          context_kind=excluded.context_kind,observed_at=excluded.observed_at""",
          (record_id,source_key,context_kind,polarity,observed_at))
        async with db.execute("""SELECT COUNT(DISTINCT CASE WHEN polarity='support' THEN source_key END),
          COUNT(DISTINCT CASE WHEN polarity='support' THEN context_kind END),
          MIN(CASE WHEN polarity='support' THEN observed_at END),MAX(CASE WHEN polarity='support' THEN observed_at END),
          COUNT(CASE WHEN polarity='contradict' THEN 1 END) FROM habit_evidence WHERE record_id=?""",
          (record_id,)) as cur: evidence=await cur.fetchone()
        samples,contexts,first,last,contradictions=evidence
        eligible=samples>=3 and contexts>=2 and first is not None and last-first>=14*86_400_000 and contradictions==0
        confidence=min(.9,.35+.12*samples-.15*contradictions)
        await db.execute("UPDATE personal_records SET status=?,confidence=?,updated_at=? WHERE record_id=?",
                         ("active" if eligible else "provisional",confidence,now,record_id)); await db.commit()
    return record_id


async def export_vault(user_id:int) -> dict:
    async with connect(user_id) as db:
        async with db.execute("SELECT record_id,kind,content,speaker,subject,authority,sensitivity,status,"
                              "source_type,source_id,confidence,explicit,valid_from,valid_to FROM personal_records "
                              "WHERE user_id=? ORDER BY created_at",(user_id,)) as cur:
            records=await cur.fetchall()
        async with db.execute("SELECT projection_id,record_id,group_id,bot_id,purpose,status,expires_at "
                              "FROM personal_projections ORDER BY created_at") as cur:
            projections=await cur.fetchall()
    fields=("record_id","kind","content","speaker","subject","authority","sensitivity","status",
            "source_type","source_id","confidence","explicit","valid_from","valid_to")
    pfields=("projection_id","record_id","group_id","bot_id","purpose","status","expires_at")
    return {"schema_version":1,"user_id":user_id,"records":[dict(zip(fields,r)) for r in records],
            "projections":[dict(zip(pfields,r)) for r in projections]}


async def delete_vault(user_id:int) -> bool:
    from runtime.dbpaths import personal_db_path
    from pathlib import Path
    path=Path(personal_db_path(user_id)); existed=path.exists()
    if existed:path.unlink()
    for suffix in ("-wal","-shm"):
        sidecar=Path(str(path)+suffix)
        if sidecar.exists():sidecar.unlink()
    return existed


async def rebuild_vault(user_id:int) -> dict:
    now=int(time.time()*1000)
    async with connect(user_id) as db:
        expired=await db.execute("UPDATE personal_projections SET status='expired',updated_at=? "
                                 "WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?",(now,now))
        await db.execute("REINDEX"); await db.commit()
    return {"expired_projections":expired.rowcount,"schema_version":1}


async def format_projected_context(*,user_id:int,group_id:int,bot_id:int|None,
                                   purpose:str="assistant_context",char_budget:int=3000)->str:
    rows=await projected_context(user_id=user_id,group_id=group_id,bot_id=bot_id,purpose=purpose)
    chunks=[];used=0
    for row in rows:
        line=f"- [{row['kind']}/{row['authority']}] {row['content']}"
        if used+len(line)>char_budget:break
        chunks.append(line);used+=len(line)
    return "[Authorized personal context]\n"+"\n".join(chunks) if chunks else ""
