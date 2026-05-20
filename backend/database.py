import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")

def get_db():
    return aiosqlite.connect(DB_PATH)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('human', 'bot')),
                role TEXT,
                system_prompt TEXT,
                avatar_color TEXT DEFAULT '#6366f1',
                FOREIGN KEY (group_id) REFERENCES groups(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES groups(id),
                FOREIGN KEY (member_id) REFERENCES members(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS role_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                summary TEXT NOT NULL,
                covered_through_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_embeddings (
                message_id INTEGER PRIMARY KEY,
                embedding TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS member_read (
                member_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                last_read_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (member_id, group_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_reactions (
                message_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                PRIMARY KEY (message_id, member_id, emoji),
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pinned_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                pinned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, message_id),
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        for col_sql in [
            "ALTER TABLE messages ADD COLUMN reply_to_id INTEGER",
            "ALTER TABLE messages ADD COLUMN edited_at TIMESTAMP",
            "ALTER TABLE messages ADD COLUMN is_deleted INTEGER DEFAULT 0",
            "ALTER TABLE messages ADD COLUMN file_url TEXT",
            "ALTER TABLE messages ADD COLUMN file_name TEXT",
            "ALTER TABLE messages ADD COLUMN file_size INTEGER",
            "ALTER TABLE messages ADD COLUMN file_type TEXT",
            "ALTER TABLE members ADD COLUMN model_provider TEXT DEFAULT 'deepseek'",
            "ALTER TABLE members ADD COLUMN model_name TEXT DEFAULT 'deepseek-chat'",
            "ALTER TABLE members ADD COLUMN auto_reply TEXT DEFAULT NULL",
            "ALTER TABLE groups ADD COLUMN announcement TEXT DEFAULT NULL",
            "ALTER TABLE messages ADD COLUMN is_auto_reply INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                pass
        await db.commit()

async def get_group(db, group_id: int):
    async with db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)) as cur:
        row = await cur.fetchone()
        if row:
            return {"id": row[0], "name": row[1], "created_at": row[2],
                    "announcement": row[3] if len(row) > 3 else None}
        return None

def _row_to_member(r):
    return {"id": r[0], "group_id": r[1], "name": r[2], "type": r[3],
            "role": r[4], "system_prompt": r[5], "avatar_color": r[6],
            "model_provider": r[7] if len(r) > 7 else "deepseek",
            "model_name": r[8] if len(r) > 8 else "deepseek-chat",
            "auto_reply": r[9] if len(r) > 9 else None}

async def get_members(db, group_id: int):
    async with db.execute("SELECT * FROM members WHERE group_id = ?", (group_id,)) as cur:
        rows = await cur.fetchall()
        return [_row_to_member(r) for r in rows]

_MSG_SQL = """
    SELECT m.id, m.group_id, m.member_id, m.content, m.created_at,
           mb.name, mb.type, mb.avatar_color,
           m.reply_to_id, rm.content, rmb.name,
           m.edited_at, m.is_deleted,
           m.file_url, m.file_name, m.file_size, m.file_type,
           m.is_auto_reply
    FROM messages m
    JOIN members mb ON m.member_id = mb.id
    LEFT JOIN messages rm ON m.reply_to_id = rm.id
    LEFT JOIN members rmb ON rm.member_id = rmb.id
"""

def _row_to_msg(r):
    reply_to = {"id": r[8], "sender_name": r[10], "content": r[9]} if r[8] else None
    return {"id": r[0], "group_id": r[1], "member_id": r[2], "content": r[3],
            "created_at": r[4], "sender_name": r[5], "sender_type": r[6],
            "avatar_color": r[7], "reply_to": reply_to,
            "edited_at": r[11], "is_deleted": bool(r[12]),
            "file_url": r[13], "file_name": r[14], "file_size": r[15], "file_type": r[16],
            "is_auto_reply": bool(r[17]) if len(r) > 17 else False}

async def get_messages(db, group_id: int, limit: int = 50, before_id: int = None):
    if before_id:
        async with db.execute(
            _MSG_SQL + "WHERE m.group_id = ? AND m.id < ? ORDER BY m.id DESC LIMIT ?",
            (group_id, before_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            _MSG_SQL + "WHERE m.group_id = ? ORDER BY m.id DESC LIMIT ?",
            (group_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    return list(reversed([_row_to_msg(r) for r in rows]))

async def get_all_messages(db, group_id: int):
    async with db.execute(
        _MSG_SQL + "WHERE m.group_id = ? ORDER BY m.id ASC",
        (group_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_msg(r) for r in rows]

async def get_member_stats(db, group_id: int):
    async with db.execute(
        """SELECT m.name, m.type, COUNT(msg.id) as cnt
           FROM members m
           LEFT JOIN messages msg ON msg.member_id = m.id AND msg.group_id = ? AND (msg.is_deleted IS NULL OR msg.is_deleted = 0)
           WHERE m.group_id = ?
           GROUP BY m.id ORDER BY cnt DESC""",
        (group_id, group_id)
    ) as cur:
        rows = await cur.fetchall()
    return [{"name": r[0], "type": r[1], "count": r[2]} for r in rows]

async def save_message(db, group_id: int, member_id: int, content: str, reply_to_id: int = None,
                       file_url: str = None, file_name: str = None, file_size: int = None, file_type: str = None,
                       is_auto_reply: bool = False):
    async with db.execute(
        "INSERT INTO messages (group_id, member_id, content, reply_to_id, file_url, file_name, file_size, file_type, is_auto_reply) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (group_id, member_id, content, reply_to_id, file_url, file_name, file_size, file_type, int(is_auto_reply))
    ) as cur:
        await db.commit()
        return cur.lastrowid

async def update_member_setting(db, member_id: int, auto_reply: str | None):
    await db.execute("UPDATE members SET auto_reply=? WHERE id=?", (auto_reply or None, member_id))
    await db.commit()

async def update_message(db, msg_id: int, content: str):
    await db.execute(
        "UPDATE messages SET content=?, edited_at=CURRENT_TIMESTAMP WHERE id=?",
        (content, msg_id)
    )
    await db.commit()

async def soft_delete_message(db, msg_id: int):
    await db.execute("UPDATE messages SET is_deleted=1 WHERE id=?", (msg_id,))
    await db.commit()

async def get_message_meta(db, msg_id: int):
    async with db.execute("SELECT id, group_id, member_id FROM messages WHERE id=?", (msg_id,)) as cur:
        row = await cur.fetchone()
        return {"id": row[0], "group_id": row[1], "member_id": row[2]} if row else None

async def toggle_reaction(db, message_id: int, member_id: int, emoji: str):
    async with db.execute(
        "SELECT 1 FROM message_reactions WHERE message_id=? AND member_id=? AND emoji=?",
        (message_id, member_id, emoji)
    ) as cur:
        exists = await cur.fetchone()
    if exists:
        await db.execute(
            "DELETE FROM message_reactions WHERE message_id=? AND member_id=? AND emoji=?",
            (message_id, member_id, emoji)
        )
    else:
        await db.execute(
            "INSERT INTO message_reactions (message_id, member_id, emoji) VALUES (?,?,?)",
            (message_id, member_id, emoji)
        )
    await db.commit()

async def get_reactions_for_message(db, message_id: int) -> dict:
    async with db.execute(
        "SELECT emoji, member_id FROM message_reactions WHERE message_id=?", (message_id,)
    ) as cur:
        rows = await cur.fetchall()
    result = {}
    for emoji, mid in rows:
        result.setdefault(emoji, []).append(mid)
    return result

async def get_reactions_for_group(db, group_id: int) -> dict:
    async with db.execute("""
        SELECT mr.message_id, mr.emoji, mr.member_id
        FROM message_reactions mr
        JOIN messages m ON mr.message_id = m.id
        WHERE m.group_id = ?
    """, (group_id,)) as cur:
        rows = await cur.fetchall()
    result = {}
    for msg_id, emoji, mid in rows:
        result.setdefault(str(msg_id), {}).setdefault(emoji, []).append(mid)
    return result

async def pin_message(db, group_id: int, message_id: int):
    await db.execute(
        "INSERT OR IGNORE INTO pinned_messages (group_id, message_id) VALUES (?, ?)",
        (group_id, message_id)
    )
    await db.commit()

async def unpin_message(db, group_id: int, message_id: int):
    await db.execute(
        "DELETE FROM pinned_messages WHERE group_id=? AND message_id=?",
        (group_id, message_id)
    )
    await db.commit()

async def get_pinned_messages(db, group_id: int):
    async with db.execute(
        _MSG_SQL + """
        JOIN pinned_messages pm ON m.id = pm.message_id
        WHERE pm.group_id = ?
        ORDER BY pm.pinned_at DESC
        """,
        (group_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_msg(r) for r in rows]

async def get_member(db, member_id: int):
    async with db.execute("SELECT * FROM members WHERE id = ?", (member_id,)) as cur:
        row = await cur.fetchone()
        return _row_to_member(row) if row else None
