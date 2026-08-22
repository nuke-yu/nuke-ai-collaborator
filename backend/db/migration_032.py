"""Migration 032: durable execution identity for tool traces."""


async def apply(db, safe_add_column) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_events'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS agent_runs (
        run_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL, bot_id INTEGER,
        thread_id TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','failed','cancelled','abandoned')),
        provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', executor TEXT NOT NULL DEFAULT '',
        started_at INTEGER NOT NULL, completed_at INTEGER, iterations INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
        error_summary TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL)""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_group_started ON agent_runs(group_id, started_at DESC)")
    for column in ("run_id TEXT NOT NULL DEFAULT ''", "step_id TEXT NOT NULL DEFAULT ''", "attempt_id TEXT NOT NULL DEFAULT ''"):
        await safe_add_column(db, f"ALTER TABLE tool_events ADD COLUMN {column}")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_events_run_step ON tool_events(group_id, run_id, step_id)")
    await db.commit()
