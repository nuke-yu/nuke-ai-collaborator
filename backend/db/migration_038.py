"""Migration 038: temporal and usage metadata for Memory records."""


async def apply(db, safe_add_column) -> None:
    for statement in (
        "ALTER TABLE memory_records ADD COLUMN supporting_count INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE memory_records ADD COLUMN contradicting_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE memory_records ADD COLUMN last_used_at INTEGER",
        "ALTER TABLE memory_records ADD COLUMN valid_to INTEGER",
        "ALTER TABLE memory_records ADD COLUMN superseded_by TEXT",
    ):
        await safe_add_column(db, statement)
    await db.commit()
