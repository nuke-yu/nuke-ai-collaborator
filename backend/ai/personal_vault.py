"""Physically isolated Personal Knowledge Vault and explicit Group projections."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import hashlib
import time
import aiosqlite


_DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS personal_records (
     record_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,
     speaker TEXT NOT NULL DEFAULT '',subject TEXT NOT NULL DEFAULT '',authority TEXT NOT NULL,
     sensitivity TEXT NOT NULL DEFAULT 'private',status TEXT NOT NULL DEFAULT 'active',
     source_type TEXT NOT NULL,source_id TEXT NOT NULL DEFAULT '',confidence REAL NOT NULL DEFAULT 0.5,
     explicit INTEGER NOT NULL DEFAULT 0,valid_from INTEGER NOT NULL,valid_to INTEGER,
     created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_personal_records_active ON personal_records(user_id,kind,status)",
    """CREATE TABLE IF NOT EXISTS personal_projections (
     projection_id TEXT PRIMARY KEY,record_id TEXT NOT NULL,group_id INTEGER NOT NULL,bot_id INTEGER,
     purpose TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',expires_at INTEGER,
     created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(record_id,group_id,bot_id,purpose),
     FOREIGN KEY(record_id) REFERENCES personal_records(record_id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS personal_sources (
     source_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,source_type TEXT NOT NULL,source_id TEXT NOT NULL,
     speaker TEXT NOT NULL DEFAULT '',subject TEXT NOT NULL DEFAULT '',context_kind TEXT NOT NULL DEFAULT '',
     observed_at INTEGER NOT NULL,content_hash TEXT NOT NULL,created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS personal_memory_usage_events (
     usage_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
     record_id TEXT NOT NULL,projection_id TEXT NOT NULL,group_id INTEGER NOT NULL,
     bot_id INTEGER,session_id TEXT NOT NULL DEFAULT '',purpose TEXT NOT NULL,
     used_at INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_personal_memory_usage_record ON personal_memory_usage_events(user_id,record_id,used_at)",
    "CREATE INDEX IF NOT EXISTS idx_personal_memory_usage_session ON personal_memory_usage_events(group_id,session_id,used_at)",
    """CREATE TABLE IF NOT EXISTS personal_acl_audit_events (
     audit_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
     actor_id TEXT NOT NULL,scope_kind TEXT NOT NULL,group_id INTEGER,
     bot_id INTEGER,action TEXT NOT NULL,allowed INTEGER NOT NULL,
     reason TEXT NOT NULL,created_at INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_personal_acl_audit_user_time ON personal_acl_audit_events(user_id,created_at)",
    """CREATE TABLE IF NOT EXISTS personal_access_controls (
     rule_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
     subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,
     object_type TEXT NOT NULL,object_id TEXT NOT NULL,
     effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
     created_at INTEGER NOT NULL,
     UNIQUE(user_id,subject_type,subject_id,object_type,object_id))""",
    "CREATE INDEX IF NOT EXISTS idx_personal_acl_object ON personal_access_controls(user_id,object_type,object_id)",
    """CREATE TABLE IF NOT EXISTS personal_apps (
     app_id TEXT NOT NULL,user_id INTEGER NOT NULL,name TEXT NOT NULL,
     status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
     created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
     PRIMARY KEY(user_id,app_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_personal_apps_user_status ON personal_apps(user_id,status)",
    """CREATE TABLE IF NOT EXISTS habit_evidence (
     id INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,source_key TEXT NOT NULL,
     context_kind TEXT NOT NULL,polarity TEXT NOT NULL,observed_at INTEGER NOT NULL,
     UNIQUE(record_id,source_key),
     FOREIGN KEY(record_id) REFERENCES personal_records(record_id) ON DELETE CASCADE)""",
    "CREATE TABLE IF NOT EXISTS _schema_version(version INTEGER NOT NULL,applied_at INTEGER NOT NULL)",
)

_MIGRATE_V2 = """
PRAGMA foreign_keys=OFF;
BEGIN IMMEDIATE;
CREATE TABLE personal_projections_v2 (
 projection_id TEXT PRIMARY KEY,record_id TEXT NOT NULL,group_id INTEGER NOT NULL,bot_id INTEGER,
 purpose TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',expires_at INTEGER,
 created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(record_id,group_id,bot_id,purpose),
 FOREIGN KEY(record_id) REFERENCES personal_records(record_id) ON DELETE CASCADE);
INSERT INTO personal_projections_v2
 SELECT p.* FROM personal_projections p JOIN personal_records r ON r.record_id=p.record_id;
CREATE TABLE habit_evidence_v2 (
 id INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,source_key TEXT NOT NULL,
 context_kind TEXT NOT NULL,polarity TEXT NOT NULL,observed_at INTEGER NOT NULL,
 UNIQUE(record_id,source_key),
 FOREIGN KEY(record_id) REFERENCES personal_records(record_id) ON DELETE CASCADE);
INSERT INTO habit_evidence_v2
 SELECT h.* FROM habit_evidence h JOIN personal_records r ON r.record_id=h.record_id;
DROP TABLE personal_projections;
DROP TABLE habit_evidence;
ALTER TABLE personal_projections_v2 RENAME TO personal_projections;
ALTER TABLE habit_evidence_v2 RENAME TO habit_evidence;
DELETE FROM _schema_version;
INSERT INTO _schema_version(version,applied_at)
 VALUES(2,CAST(strftime('%s','now') AS INTEGER)*1000);
COMMIT;
PRAGMA foreign_keys=ON;
"""


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    for stmt in _DDL_STATEMENTS:
        await db.execute(stmt)
    await db.commit()
    async with db.execute("SELECT MAX(version) FROM _schema_version") as cur:
        version = (await cur.fetchone())[0]
    if version is None:
        await db.execute(
            "INSERT INTO _schema_version(version,applied_at) VALUES(2,?)",
            (int(time.time() * 1000),),
        )
        await db.commit()
    elif version < 2:
        await db.executescript(_MIGRATE_V2)
    async with db.execute("PRAGMA foreign_key_check") as cur:
        if await cur.fetchone() is not None:
            raise RuntimeError("personal vault foreign key check failed")


import weakref


_vault_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()

def _get_vault_lock(user_id: int) -> asyncio.Lock:
    lock = _vault_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _vault_locks[user_id] = lock
    return lock


@asynccontextmanager
async def connect(user_id: int):
    lock = _get_vault_lock(user_id)
    async with lock:
        from runtime.dbpaths import personal_db_path
        path = personal_db_path(user_id)
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(path, timeout=5.0)
        try:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("PRAGMA foreign_keys=ON")
            async with db.execute("PRAGMA journal_mode") as cur:
                journal_mode = (await cur.fetchone())[0]
            if str(journal_mode).lower() != "wal":
                await db.execute("PRAGMA journal_mode=WAL")
            await _ensure_schema(db)
            yield db
        finally:
            await db.close()


async def record_acl_audit_event(
    *,
    user_id: int,
    actor_id: str,
    scope_kind: str,
    group_id: int | None,
    bot_id: int | None,
    action: str,
    allowed: bool,
    reason: str,
) -> None:
    """Persist an authorization decision without storing memory content."""
    async with connect(user_id) as db:
        await db.execute(
            """INSERT INTO personal_acl_audit_events
               (user_id,actor_id,scope_kind,group_id,bot_id,action,allowed,reason,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                actor_id,
                scope_kind,
                group_id,
                bot_id,
                action,
                int(allowed),
                reason[:1000],
                int(time.time() * 1000),
            ),
        )
        await db.commit()


async def set_access_control_rule(
    *,
    user_id: int,
    subject_type: str,
    subject_id: str,
    object_type: str,
    object_id: str,
    effect: str,
) -> int:
    """Create/update an OpenMemory-style subject/object/effect rule."""
    if effect not in {"allow", "deny"}:
        raise ValueError("effect must be allow or deny")
    values = tuple(
        value.strip()
        for value in (subject_type, subject_id, object_type, object_id)
    )
    if not all(values):
        raise ValueError("subject and object fields are required")
    async with connect(user_id) as db:
        await db.execute(
            """INSERT INTO personal_access_controls
               (user_id,subject_type,subject_id,object_type,object_id,effect,created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id,subject_type,subject_id,object_type,object_id)
               DO UPDATE SET effect=excluded.effect,created_at=excluded.created_at""",
            (user_id, *values, effect, int(time.time() * 1000)),
        )
        await db.commit()
        async with db.execute(
            """SELECT rule_id FROM personal_access_controls
               WHERE user_id=? AND subject_type=? AND subject_id=?
                 AND object_type=? AND object_id=?""",
            (user_id, *values),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0])


async def register_personal_app(*, user_id: int, app_id: str, name: str) -> None:
    """Create or reactivate an OpenMemory-style app registration."""
    app_id, name = app_id.strip(), name.strip()
    if not app_id or not name:
        raise ValueError("app_id and name are required")
    now = int(time.time() * 1000)
    async with connect(user_id) as db:
        await db.execute(
            """INSERT INTO personal_apps(app_id,user_id,name,status,created_at,updated_at)
               VALUES(?,?,?,'active',?,?)
               ON CONFLICT(user_id,app_id) DO UPDATE SET name=excluded.name,status='active',updated_at=excluded.updated_at""",
            (app_id, user_id, name, now, now),
        )
        await db.commit()


async def set_personal_app_status(*, user_id: int, app_id: str, active: bool) -> bool:
    """Activate/deactivate an app; deactivated apps cannot project memory."""
    async with connect(user_id) as db:
        cur = await db.execute(
            "UPDATE personal_apps SET status=?,updated_at=? WHERE user_id=? AND app_id=?",
            ("active" if active else "inactive", int(time.time() * 1000), user_id, app_id.strip()),
        )
        await db.commit()
    return cur.rowcount == 1


async def is_personal_app_active(*, user_id: int, app_id: str) -> bool:
    async with connect(user_id) as db:
        async with db.execute(
            "SELECT status FROM personal_apps WHERE user_id=? AND app_id=?",
            (user_id, app_id.strip()),
        ) as cur:
            row = await cur.fetchone()
    return bool(row and row[0] == "active")


async def evaluate_access_control_rule(
    *,
    user_id: int,
    subject_type: str,
    subject_id: str,
    object_type: str,
    object_id: str,
) -> bool | None:
    """Return explicit ABAC decision, or None when no rule matches.

    Exact subject/object rules outrank wildcard rules. If multiple matching
    rules have the same specificity, deny wins so a broad deny cannot be
    weakened by another allow rule.
    """
    subject_type = subject_type.strip()
    subject_id = subject_id.strip()
    object_type = object_type.strip()
    object_id = object_id.strip()
    async with connect(user_id) as db:
        async with db.execute(
            """SELECT subject_type,subject_id,object_type,object_id,effect
               FROM personal_access_controls
               WHERE user_id=?
                 AND subject_type IN (?, '*')
                 AND subject_id IN (?, '*')
                 AND object_type IN (?, '*')
                 AND object_id IN (?, '*')""",
            (user_id, subject_type, subject_id, object_type, object_id),
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        return None
    def specificity(row) -> int:
        return sum(value != "*" for value in row[:4])

    highest = max(specificity(row) for row in rows)
    top = [row for row in rows if specificity(row) == highest]
    return not any(str(row[4]) == "deny" for row in top)


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


_vault_locks: dict[int, asyncio.Lock] = {}

def _get_vault_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _vault_locks:
        _vault_locks[user_id] = asyncio.Lock()
    return _vault_locks[user_id]


async def project(*, user_id: int, record_id: str, group_id: int, bot_id: int | None, purpose: str,
                  expires_at: int | None = None, allow_restricted: bool = False) -> str:
    async with connect(user_id) as db:
        async with db.execute(
            "SELECT sensitivity, status, explicit FROM personal_records WHERE record_id=? AND user_id=?",
            (record_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise ValueError("personal record not found")
        sensitivity, status, explicit = str(row[0]), str(row[1]), bool(row[2])
        if sensitivity == "secret":
            raise ValueError("secret personal knowledge cannot be projected")
        if sensitivity == "restricted" and not (allow_restricted or explicit):
            raise ValueError("restricted personal knowledge requires explicit confirmation")
        if status not in {"active", "provisional"}:
            raise ValueError("inactive record cannot be projected")

        key = f"{record_id}:{group_id}:{bot_id}:{purpose}"
        projection_id = "projection:" + hashlib.sha256(key.encode()).hexdigest()[:24]
        now = int(time.time() * 1000)
        await db.execute("""INSERT INTO personal_projections
          (projection_id,record_id,group_id,bot_id,purpose,expires_at,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(projection_id)
          DO UPDATE SET status='active',expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
          (projection_id, record_id, group_id, bot_id, purpose, expires_at, now, now))
        await db.commit()
    return projection_id


async def projected_context(*, user_id: int, group_id: int, bot_id: int | None,
                            purpose: str, limit: int = 20,
                            session_id: str = "") -> list[dict]:
    now = int(time.time() * 1000)
    async with connect(user_id) as db:
        async with db.execute("""SELECT r.record_id,p.projection_id,r.kind,r.content,r.authority,r.confidence,r.status,r.explicit
          FROM personal_projections p JOIN personal_records r ON r.record_id=p.record_id
          WHERE r.user_id=? AND p.group_id=? AND (p.bot_id IS NULL OR p.bot_id=?) AND p.purpose=?
          AND p.status='active' AND r.status IN ('active','provisional') AND r.sensitivity != 'secret'
          AND (p.expires_at IS NULL OR p.expires_at>?) ORDER BY r.explicit DESC,r.confidence DESC LIMIT ?""",
          (user_id, group_id, bot_id, purpose, now, max(1, min(limit, 100)))) as cur:
            rows = await cur.fetchall()
    async with connect(user_id) as db:
        await db.executemany(
            """INSERT INTO personal_memory_usage_events
               (user_id,record_id,projection_id,group_id,bot_id,session_id,purpose,used_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            [
                (user_id, row[0], row[1], group_id, bot_id, session_id, purpose, now)
                for row in rows
            ],
        )
        await db.commit()
    return [{
        "record_id": r[0],
        "projection_id": r[1],
        "kind": r[2],
        "content": r[3],
        "authority": r[4],
        "confidence": r[5],
        "status": r[6],
        "explicit": bool(r[7]),
    } for r in rows]


async def ingest_knowledge(*, user_id: int, kind: str, statement: str, source_type: str, source_id: str,
                            speaker: str, subject: str, context_kind: str, observed_at: int | None = None,
                            asserted_by_user: bool = False, sensitivity: str = "private") -> str:
    """Ingest an extracted statement, never an email/chat credential or raw mailbox dump."""
    observed = observed_at or int(time.time() * 1000)
    authority = "user_statement" if asserted_by_user and str(subject) == str(user_id) else (
        "third_party" if str(subject) != str(user_id) else "observed")
    record_id = await add_record(user_id=user_id, kind=kind, content=statement, source_type=source_type,
                                 source_id=source_id, speaker=speaker, subject=subject, authority=authority,
                                 sensitivity=sensitivity, confidence=1.0 if authority == "user_statement" else .45,
                                 explicit=authority == "user_statement")
    source_key = f"{source_type}:{source_id}"
    async with connect(user_id) as db:
        await db.execute("""INSERT INTO personal_sources
          (source_key,user_id,source_type,source_id,speaker,subject,context_kind,observed_at,content_hash,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO NOTHING""",
          (source_key, user_id, source_type, source_id, speaker, subject, context_kind, observed,
           hashlib.sha256(statement.encode()).hexdigest(), int(time.time() * 1000)))
        await db.commit()
    return record_id


async def observe_habit(*, user_id: int, habit_key: str, statement: str, source_type: str, source_id: str,
                        context_kind: str, observed_at: int, polarity: str = "support") -> str:
    if polarity not in {"support", "contradict"}:
        raise ValueError("invalid habit evidence polarity")
    record_id = "habit:" + hashlib.sha256(f"{user_id}:{habit_key}".encode()).hexdigest()[:24]
    now = int(time.time() * 1000)
    source_key = f"{source_type}:{source_id}"
    async with connect(user_id) as db:
        await db.execute("""INSERT INTO personal_records
          (record_id,user_id,kind,content,authority,sensitivity,status,source_type,source_id,
           confidence,explicit,valid_from,created_at,updated_at) VALUES(?,?,'habit',?,'observed','private',
           'provisional',?,?,.35,0,?,?,?) ON CONFLICT(record_id) DO UPDATE SET updated_at=excluded.updated_at""",
          (record_id, user_id, statement, source_type, source_id, observed_at, now, now))
        await db.execute("""INSERT INTO habit_evidence(record_id,source_key,context_kind,polarity,observed_at)
          VALUES(?,?,?,?,?) ON CONFLICT(record_id,source_key) DO UPDATE SET polarity=excluded.polarity,
          context_kind=excluded.context_kind,observed_at=excluded.observed_at""",
          (record_id, source_key, context_kind, polarity, observed_at))
        async with db.execute("""SELECT COUNT(DISTINCT CASE WHEN polarity='support' THEN source_key END),
          COUNT(DISTINCT CASE WHEN polarity='support' THEN context_kind END),
          MIN(CASE WHEN polarity='support' THEN observed_at END),MAX(CASE WHEN polarity='support' THEN observed_at END),
          COUNT(CASE WHEN polarity='contradict' THEN 1 END) FROM habit_evidence WHERE record_id=?""",
          (record_id,)) as cur:
            evidence = await cur.fetchone()
        samples, contexts, first, last, contradictions = evidence
        eligible = samples >= 3 and contexts >= 2 and first is not None and last - first >= 14 * 86_400_000 and contradictions == 0
        confidence = min(.9, .35 + .12 * samples - .15 * contradictions)
        await db.execute("UPDATE personal_records SET status=?,confidence=?,updated_at=? WHERE record_id=?",
                         ("active" if eligible else "provisional", confidence, now, record_id))
        await db.commit()
    return record_id


async def export_vault(user_id: int) -> dict:
    async with connect(user_id) as db:
        async with db.execute("SELECT record_id,kind,content,speaker,subject,authority,sensitivity,status,"
                              "source_type,source_id,confidence,explicit,valid_from,valid_to FROM personal_records "
                              "WHERE user_id=? ORDER BY created_at", (user_id,)) as cur:
            records = await cur.fetchall()
        async with db.execute("SELECT projection_id,record_id,group_id,bot_id,purpose,status,expires_at "
                              "FROM personal_projections ORDER BY created_at") as cur:
            projections = await cur.fetchall()
        async with db.execute(
            """SELECT usage_id,record_id,projection_id,group_id,bot_id,session_id,purpose,used_at
               FROM personal_memory_usage_events ORDER BY used_at DESC LIMIT 500"""
        ) as cur:
            usage_events = await cur.fetchall()
    fields = ("record_id", "kind", "content", "speaker", "subject", "authority", "sensitivity", "status",
              "source_type", "source_id", "confidence", "explicit", "valid_from", "valid_to")
    pfields = ("projection_id", "record_id", "group_id", "bot_id", "purpose", "status", "expires_at")
    ufields = ("usage_id", "record_id", "projection_id", "group_id", "bot_id", "session_id", "purpose", "used_at")
    return {"schema_version": 1, "user_id": user_id, "records": [dict(zip(fields, r)) for r in records],
            "projections": [dict(zip(pfields, r)) for r in projections],
            "usage_events": [dict(zip(ufields, r)) for r in usage_events]}


async def delete_vault(user_id: int) -> bool:
    """Safe, concurrency-guarded personal vault deletion with zero-content audit logging."""
    from runtime.dbpaths import personal_db_path
    from pathlib import Path
    import logging

    audit_log = logging.getLogger("audit.personal_vault")
    lock = _get_vault_lock(user_id)

    async with lock:
        path = Path(personal_db_path(user_id))
        existed = path.exists()
        if existed:
            path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                sidecar.unlink(missing_ok=True)

        now_ms = int(time.time() * 1000)
        audit_log.info(
            "personal_vault_deleted user_id=%d existed=%s deleted_at=%d",
            user_id, existed, now_ms,
        )
        return existed


async def delete_record(user_id: int, record_id: str) -> bool:
    """Delete a specific personal record and its projections from the vault."""
    async with connect(user_id) as db:
        cur = await db.execute(
            "DELETE FROM personal_records WHERE user_id = ? AND record_id = ?",
            (user_id, record_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def revoke_projection(user_id: int, projection_id: str) -> bool:
    """Revoke/delete a specific memory projection to a group or bot."""
    async with connect(user_id) as db:
        cur = await db.execute(
            "DELETE FROM personal_projections WHERE projection_id = ?",
            (projection_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def rebuild_vault(user_id:int) -> dict:
    now=int(time.time()*1000)
    async with connect(user_id) as db:
        expired=await db.execute("UPDATE personal_projections SET status='expired',updated_at=? "
                                 "WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?",(now,now))
        await db.execute("REINDEX"); await db.commit()
    return {"expired_projections":expired.rowcount,"schema_version":1}


async def format_projected_context(*,user_id:int,group_id:int,bot_id:int|None,
                                   purpose:str="assistant_context",char_budget:int=3000,
                                   session_id: str = "")->str:
    rows=await projected_context(user_id=user_id,group_id=group_id,bot_id=bot_id,
                                 purpose=purpose, session_id=session_id)
    chunks=[];used=0
    for row in rows:
        line=f"- [{row['kind']}/{row['authority']}] {row['content']}"
        if used+len(line)>char_budget:break
        chunks.append(line);used+=len(line)
    return "[Authorized personal context]\n"+"\n".join(chunks) if chunks else ""


async def list_memory_usage(user_id: int, *, record_id: str | None = None,
                            group_id: int | None = None, limit: int = 100) -> list[dict]:
    """Return provenance for personal records used by projected contexts."""
    where = ["user_id = ?"]
    params: list[object] = [user_id]
    if record_id:
        where.append("record_id = ?")
        params.append(record_id)
    if group_id is not None:
        where.append("group_id = ?")
        params.append(group_id)
    params.append(max(1, min(int(limit), 500)))
    async with connect(user_id) as db:
        async with db.execute(
            f"SELECT usage_id,record_id,projection_id,group_id,bot_id,session_id,purpose,used_at "
            f"FROM personal_memory_usage_events WHERE {' AND '.join(where)} ORDER BY used_at DESC LIMIT ?",
            params,
        ) as cur:
            rows = await cur.fetchall()
    fields = ("usage_id", "record_id", "projection_id", "group_id", "bot_id", "session_id", "purpose", "used_at")
    return [dict(zip(fields, row)) for row in rows]


async def get_record_impact(user_id: int, record_id: str, *, limit: int = 500) -> dict:
    """Return the groups, sessions, projections, and usage events affected by a record."""
    async with connect(user_id) as db:
        async with db.execute(
            """SELECT projection_id,group_id,bot_id,purpose,status,expires_at
               FROM personal_projections WHERE record_id=? ORDER BY created_at DESC""",
            (record_id,),
        ) as cur:
            projection_rows = await cur.fetchall()
        async with db.execute(
            """SELECT usage_id,projection_id,group_id,bot_id,session_id,purpose,used_at
               FROM personal_memory_usage_events WHERE user_id=? AND record_id=?
               ORDER BY used_at DESC LIMIT ?""",
            (user_id, record_id, max(1, min(int(limit), 500))),
        ) as cur:
            usage_rows = await cur.fetchall()

    projections = [
        {"projection_id": row[0], "group_id": row[1], "bot_id": row[2],
         "purpose": row[3], "status": row[4], "expires_at": row[5]}
        for row in projection_rows
    ]
    usage_events = [
        {"usage_id": row[0], "projection_id": row[1], "group_id": row[2],
         "bot_id": row[3], "session_id": row[4], "purpose": row[5], "used_at": row[6]}
        for row in usage_rows
    ]
    return {
        "record_id": record_id,
        "active_projections": [p for p in projections if p["status"] == "active"],
        "projections": projections,
        "usage_events": usage_events,
        "affected_group_ids": sorted({p["group_id"] for p in projections} | {e["group_id"] for e in usage_events}),
        "affected_session_ids": sorted({e["session_id"] for e in usage_events if e["session_id"]}),
    }
