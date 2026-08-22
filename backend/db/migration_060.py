"""Migration 060: persist immutable Session capability identity."""


async def apply(db, safe_add_column) -> None:
    for statement in (
        "ALTER TABLE agent_sessions ADD COLUMN manifest_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE agent_sessions ADD COLUMN manifest_hash TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE agent_sessions ADD COLUMN manifest_version INTEGER NOT NULL DEFAULT 1",
    ):
        await safe_add_column(db, statement)
    await db.commit()
