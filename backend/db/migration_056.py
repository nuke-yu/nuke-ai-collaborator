"""Migration 056: group-local payload artifacts for observability."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_events'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS observation_artifacts (
        artifact_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL, event_id TEXT NOT NULL,
        payload_policy TEXT NOT NULL, content_sha256 TEXT NOT NULL,
        byte_size INTEGER NOT NULL, content_json TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_observation_artifacts_event ON observation_artifacts(group_id,event_id)")
    await db.commit()
