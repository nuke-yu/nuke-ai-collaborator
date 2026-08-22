"""Migration 050: canonical provenance and temporal Memory relations."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_records'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_relations (
        relation_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
        from_record_id TEXT NOT NULL, to_record_id TEXT NOT NULL,
        relation_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        source_type TEXT NOT NULL, source_id TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL,
        effective_from INTEGER NOT NULL, valid_to INTEGER, created_at INTEGER NOT NULL,
        UNIQUE(group_id,from_record_id,to_record_id,relation_type,source_type,source_id)
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memory_relations_from ON memory_relations(group_id,from_record_id,status,relation_type)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memory_relations_to ON memory_relations(group_id,to_record_id,status,relation_type)")
    await db.commit()
