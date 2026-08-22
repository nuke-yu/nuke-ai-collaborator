"""Migration 058: exact request-level model usage ledger."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_events'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS model_usage_ledger (
        request_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,request_ordinal INTEGER NOT NULL,
        retry_of TEXT NOT NULL DEFAULT '',operation TEXT NOT NULL DEFAULT 'inference',
        ticket_id TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL,model TEXT NOT NULL,
        streaming INTEGER NOT NULL DEFAULT 0 CHECK(streaming IN (0,1)),
        status TEXT NOT NULL CHECK(status IN ('started','completed','failed')),
        response_type TEXT NOT NULL DEFAULT '',input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens>=0),
        output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens>=0),
        cache_read_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_read_tokens>=0),
        cache_creation_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_creation_tokens>=0),
        cost_usd REAL NOT NULL DEFAULT 0,pricing_version INTEGER NOT NULL DEFAULT 1,
        duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(duration_ms>=0),error_type TEXT NOT NULL DEFAULT '',
        start_event_id INTEGER NOT NULL,final_event_id INTEGER,
        started_at TEXT DEFAULT (datetime('now')),completed_at TEXT,
        UNIQUE(session_id,request_ordinal),
        FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE,
        FOREIGN KEY (start_event_id) REFERENCES session_events(id),
        FOREIGN KEY (final_event_id) REFERENCES session_events(id)
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_model_usage_session ON model_usage_ledger(session_id,request_ordinal)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_model_usage_status ON model_usage_ledger(status,started_at)")
    await db.commit()
