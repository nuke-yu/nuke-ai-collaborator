"""Migration 057: link Session Events to Memory/Skill evidence."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_events'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS session_evidence_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_event_id INTEGER NOT NULL, session_id TEXT NOT NULL,
        evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('memory','skill')),
        evidence_ref TEXT NOT NULL,
        relation TEXT NOT NULL CHECK(relation IN ('injected','cited','invoked')),
        metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(session_event_id,evidence_kind,evidence_ref,relation),
        FOREIGN KEY (session_event_id) REFERENCES session_events(id) ON DELETE CASCADE,
        FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_session_evidence_ref ON session_evidence_links(evidence_ref,id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_session_evidence_session ON session_evidence_links(session_id,session_event_id)")
    await db.commit()
