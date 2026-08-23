"""Migration 030: tokenized retry claims for agent tasks."""


async def apply(db) -> None:
    await db.execute("""CREATE TABLE IF NOT EXISTS agent_task_retry_claims (
        task_id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE,
        previous_status TEXT NOT NULL, automatic INTEGER NOT NULL DEFAULT 0,
        claimed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
    )""")
    await db.commit()
