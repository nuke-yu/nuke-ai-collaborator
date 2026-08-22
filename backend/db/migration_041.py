"""Migration 041: persist Skill usage state and backfill executed rows."""


async def apply(db, safe_add_column) -> None:
    await safe_add_column(db, "ALTER TABLE skill_usage ADD COLUMN state TEXT NOT NULL DEFAULT 'injected'")
    await db.execute("UPDATE skill_usage SET state='executed' WHERE outcome IS NOT NULL AND outcome!=''")
    await db.commit()
