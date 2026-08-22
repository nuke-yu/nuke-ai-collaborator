"""Migration 033: durable Agent Case records."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_runs'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS agent_cases (
        case_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, group_id INTEGER NOT NULL,
        bot_id INTEGER, task TEXT NOT NULL DEFAULT '', task_signature TEXT NOT NULL DEFAULT '',
        tools_used TEXT NOT NULL DEFAULT '[]', files_touched TEXT NOT NULL DEFAULT '[]',
        attempts INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]',
        outcome TEXT NOT NULL, outcome_confidence REAL NOT NULL DEFAULT 0.0,
        verification_signals TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_cases_group_created ON agent_cases(group_id, created_at DESC)")
    await db.commit()
