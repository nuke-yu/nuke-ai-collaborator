"""Migration 036: durable pipeline jobs."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_cases'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS pipeline_jobs (
        job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, group_id INTEGER NOT NULL,
        input_id TEXT NOT NULL, input_version TEXT NOT NULL DEFAULT '1',
        status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3, idempotency_key TEXT NOT NULL UNIQUE,
        lease_until INTEGER, error TEXT NOT NULL DEFAULT '', output_json TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER)""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_ready ON pipeline_jobs(group_id,status,updated_at)")
    await db.commit()
