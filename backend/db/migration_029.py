"""Migration 029: idempotency reservations for agent task creation."""


async def apply(db) -> None:
    await db.execute("""CREATE TABLE IF NOT EXISTS agent_task_requests (
        idempotency_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
        task_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending', 'completed', 'failed')),
        error_message TEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.commit()
