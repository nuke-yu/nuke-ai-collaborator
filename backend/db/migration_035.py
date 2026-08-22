"""Migration 035: Experience usage execution metrics."""


async def apply(db, safe_add_column) -> None:
    for statement in (
        "ALTER TABLE experience_usage ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE experience_usage ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE experience_usage ADD COLUMN tool_attempts INTEGER NOT NULL DEFAULT 0",
    ):
        await safe_add_column(db, statement)
    await db.commit()
