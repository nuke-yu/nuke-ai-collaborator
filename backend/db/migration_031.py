"""Migration 031: control-plane operator authorization."""


async def apply(db, safe_add_column) -> None:
    await safe_add_column(db, "ALTER TABLE users ADD COLUMN is_operator INTEGER NOT NULL DEFAULT 0 CHECK(is_operator IN (0, 1))")
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'")
    if await cur.fetchone() is not None:
        cur = await db.execute("SELECT 1 FROM users WHERE is_operator = 1 LIMIT 1")
        if await cur.fetchone() is None:
            await db.execute("""UPDATE users SET is_operator = 1 WHERE id = COALESCE(
                (SELECT id FROM users WHERE username = 'Nuke' LIMIT 1),
                (SELECT MIN(id) FROM users))""")
    await db.commit()
