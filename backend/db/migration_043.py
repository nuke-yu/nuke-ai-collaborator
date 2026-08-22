"""Migration 043: group memberships and legacy single-user backfill."""


async def apply(db) -> None:
    cur = await db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('users','groups')")
    if (await cur.fetchone())[0] != 2:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS group_memberships (
        user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
        role TEXT NOT NULL DEFAULT 'member', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id,group_id),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE)""")
    await db.execute("""INSERT INTO group_memberships(user_id,group_id,role)
        SELECT u.id,g.id,'owner' FROM users u CROSS JOIN groups g
        WHERE (SELECT COUNT(*) FROM users)=1
        ON CONFLICT(user_id,group_id) DO NOTHING""")
    await db.commit()
