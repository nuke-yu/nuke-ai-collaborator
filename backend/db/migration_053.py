"""Migration 053: validated learned-memory references in traces."""


async def apply(db, safe_add_column) -> None:
    await safe_add_column(db, "ALTER TABLE tool_events ADD COLUMN memory_refs_json TEXT NOT NULL DEFAULT '[]'")
    await safe_add_column(db, "ALTER TABLE run_decisions ADD COLUMN memory_refs_json TEXT NOT NULL DEFAULT '[]'")
    await db.commit()
