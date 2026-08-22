"""Migration 059: payload-free observability retention receipts."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_events'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS observability_retention_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL CHECK(source IN ('session','workflow')),
        source_row_id INTEGER NOT NULL,event_id TEXT NOT NULL,event_type TEXT NOT NULL,
        retention TEXT NOT NULL,occurred_at INTEGER NOT NULL,content_sha256 TEXT NOT NULL,
        archived_at TEXT DEFAULT (datetime('now')),UNIQUE(source,source_row_id)
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_retention_archive_policy
        ON observability_retention_archive(retention,archived_at)""")
    await db.commit()
