"""Migration 039: Skill registry, versions, and usage tables."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_records'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS skills (
        skill_id TEXT PRIMARY KEY,group_id INTEGER NOT NULL,bot_id INTEGER,name TEXT NOT NULL,
        maturity TEXT NOT NULL DEFAULT 'candidate',risk_level TEXT NOT NULL,
        current_version INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'active',
        success_count INTEGER NOT NULL DEFAULT 0,failure_count INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(group_id,bot_id,name))""")
    await db.execute("""CREATE TABLE IF NOT EXISTS skill_versions (
        skill_id TEXT NOT NULL,version INTEGER NOT NULL,schema_version TEXT NOT NULL DEFAULT '1',
        declaration_json TEXT NOT NULL,content_hash TEXT NOT NULL,evidence_ids TEXT NOT NULL DEFAULT '[]',
        created_at INTEGER NOT NULL,PRIMARY KEY(skill_id,version))""")
    await db.execute("""CREATE TABLE IF NOT EXISTS skill_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,skill_id TEXT NOT NULL,version INTEGER NOT NULL,
        run_id TEXT NOT NULL,group_id INTEGER NOT NULL,outcome TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,
        UNIQUE(skill_id,run_id))""")
    await db.commit()
