"""Migration 054: structured evidence for Memory adoption decisions."""


async def apply(db, safe_add_column) -> None:
    await safe_add_column(db, "ALTER TABLE run_decisions ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'")
    await db.commit()
