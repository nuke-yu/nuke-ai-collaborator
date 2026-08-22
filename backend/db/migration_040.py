"""Migration 040: durable pipeline lease token."""


async def apply(db, safe_add_column) -> None:
    await safe_add_column(db, "ALTER TABLE pipeline_jobs ADD COLUMN lease_token TEXT DEFAULT NULL")
    await db.commit()
