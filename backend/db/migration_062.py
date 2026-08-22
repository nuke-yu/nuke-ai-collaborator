"""Migration 062: deduplicate replayed channel ingress."""


async def apply(db, safe_add_column) -> None:
    await safe_add_column(db, "ALTER TABLE messages ADD COLUMN external_message_key TEXT DEFAULT NULL")
    cursor = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'")
    if await cursor.fetchone() is not None:
        await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external_key
            ON messages(external_message_key) WHERE external_message_key IS NOT NULL""")
    await db.commit()
