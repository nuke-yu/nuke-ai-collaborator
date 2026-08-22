"""Migration 037: durable Run decision records."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_runs'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS run_decisions (
        decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, group_id INTEGER NOT NULL,
        bot_id INTEGER, step_id TEXT NOT NULL, decision_type TEXT NOT NULL,
        failure_class TEXT NOT NULL DEFAULT '', observation TEXT NOT NULL DEFAULT '',
        corrective_plan TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
        UNIQUE(run_id,step_id,decision_type))""")
    await db.commit()
