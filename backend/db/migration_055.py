"""Migration 055: canonical workflow transition observations."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_state'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS workflow_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        observation_id TEXT NOT NULL UNIQUE, group_id INTEGER NOT NULL,
        workflow_id TEXT NOT NULL, event_type TEXT NOT NULL,
        stage_id TEXT NOT NULL DEFAULT '', gate_id TEXT NOT NULL DEFAULT '',
        gate_instance_id TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
        envelope_json TEXT NOT NULL, occurred_at INTEGER NOT NULL
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workflow_observations_flow ON workflow_observations(group_id,workflow_id,id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workflow_observations_type ON workflow_observations(group_id,event_type,occurred_at)")
    await db.commit()
