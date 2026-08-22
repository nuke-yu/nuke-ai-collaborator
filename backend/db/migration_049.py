"""Migration 049: ownership, authority, and evidence on canonical Memory."""


async def apply(db, safe_add_column) -> None:
    for declaration in (
        "owner_type TEXT NOT NULL DEFAULT 'bot'",
        "authority TEXT NOT NULL DEFAULT 'bot_observation'",
        "subject_key TEXT NOT NULL DEFAULT ''",
        "sensitivity TEXT NOT NULL DEFAULT 'group'",
        "evidence_json TEXT NOT NULL DEFAULT '{}'",
        "created_by TEXT NOT NULL DEFAULT ''",
        "effective_from INTEGER",
    ):
        await safe_add_column(db, f"ALTER TABLE memory_records ADD COLUMN {declaration}")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_memory_records_group_facts
        ON memory_records(group_id,owner_type,kind,status,subject_key,updated_at DESC)""")
    await db.commit()
