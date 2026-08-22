"""Migration 051: per-group canonical-memory projection rollout gate."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_records'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_projection_rollout (
        group_id INTEGER PRIMARY KEY,
        consecutive_passes INTEGER NOT NULL DEFAULT 0,
        required_passes INTEGER NOT NULL DEFAULT 3,
        direct_write_enabled INTEGER NOT NULL DEFAULT 1 CHECK(direct_write_enabled IN (0,1)),
        last_audit_passed INTEGER NOT NULL DEFAULT 0 CHECK(last_audit_passed IN (0,1)),
        last_audited_at INTEGER NOT NULL DEFAULT 0,
        last_failure_reason TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL DEFAULT 0
    )""")
    await db.commit()
