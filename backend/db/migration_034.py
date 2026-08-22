"""Migration 034: canonical Memory records and Experience usage."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_cases'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_records (
        record_id TEXT PRIMARY KEY, kind TEXT NOT NULL, group_id INTEGER NOT NULL,
        bot_id INTEGER, status TEXT NOT NULL DEFAULT 'active', content TEXT NOT NULL,
        task_signature TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0.0,
        importance REAL NOT NULL DEFAULT 0.0, source_ids TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}', algorithm_version TEXT NOT NULL DEFAULT 'experience-v1',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memory_records_lookup ON memory_records(group_id, bot_id, kind, status)")
    await db.execute("""CREATE TABLE IF NOT EXISTS experience_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL, run_id TEXT NOT NULL,
        group_id INTEGER NOT NULL, bot_id INTEGER, state TEXT NOT NULL,
        outcome TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0, tool_attempts INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(record_id, run_id))""")
    await db.commit()
