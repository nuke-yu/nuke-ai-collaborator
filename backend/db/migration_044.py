"""Migration 044: durable Memory projection outbox."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_records'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_projection_outbox (
        event_id TEXT PRIMARY KEY, projection_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL, aggregate_version TEXT NOT NULL,
        group_id INTEGER NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0,
        next_attempt_at INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
        lease_until INTEGER, last_error TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER)""")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_memory_projection_outbox_ready
        ON memory_projection_outbox(group_id,status,next_attempt_at,updated_at)""")
    await db.commit()
