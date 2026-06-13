import json
import logging
from db.models import _row_to_member, _row_to_msg, _MSG_SQL

log = logging.getLogger(__name__)

# Explicit column list in _row_to_member's positional order. The central
# (schema_split) members table has an extra `user_id` column that the legacy
# (schema.py) table lacks, so `SELECT *` shifts every field for central rows
# (name->user_id, type->name, ...). Selecting these columns by name keeps the
# positional mapping correct regardless of which schema the DB uses.
_MEMBER_COLS = (
    "id, group_id, name, type, role, system_prompt, avatar_color, "
    "model_provider, model_name, auto_reply, context_cleared_at, temperature, "
    "max_tokens, personality_prompt, executor_id, executor_config, done_keyword"
)


async def get_group(db, group_id: int):
    async with db.execute("SELECT id, name, created_at, announcement, assigned_worker_id, away_summary FROM groups WHERE id = ?", (group_id,)) as cur:
        row = await cur.fetchone()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "created_at": row[2],
                "announcement": row[3],
                "assigned_worker_id": row[4],
                "away_summary": row[5]
            }
        return None


async def get_members(db, group_id: int):
    async with db.execute(f"SELECT {_MEMBER_COLS} FROM members WHERE group_id = ?", (group_id,)) as cur:
        rows = await cur.fetchall()
        return [_row_to_member(r) for r in rows]


async def get_member(db, member_id: int):
    async with db.execute(f"SELECT {_MEMBER_COLS} FROM members WHERE id = ?", (member_id,)) as cur:
        row = await cur.fetchone()
        return _row_to_member(row) if row else None


async def get_messages(db, group_id: int, limit: int = 50, before_id: int = None, after_time: str = None, after_id: int = None):
    if after_id:
        async with db.execute(
            _MSG_SQL + "WHERE m.group_id = ? AND m.id > ? ORDER BY m.id ASC LIMIT ?",
            (group_id, after_id, limit)
        ) as cur:
            rows = await cur.fetchall()
            return [_row_to_msg(r) for r in rows]
    if before_id:
        async with db.execute(
            _MSG_SQL + "WHERE m.group_id = ? AND m.id < ? ORDER BY m.id DESC LIMIT ?",
            (group_id, before_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    elif after_time:
        async with db.execute(
            _MSG_SQL + "WHERE m.group_id = ? AND m.created_at > ? ORDER BY m.id DESC LIMIT ?",
            (group_id, after_time, limit)
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
           LEFT JOIN messages msg ON msg.member_id = m.id AND msg.group_id = ?
               AND (msg.is_deleted IS NULL OR msg.is_deleted = 0)
           WHERE m.group_id = ?
           GROUP BY m.id ORDER BY cnt DESC""",
        (group_id, group_id)
    ) as cur:
        rows = await cur.fetchall()
    return [{"name": r[0], "type": r[1], "count": r[2]} for r in rows]


async def _sender_snapshot(write_db, member_id: int):
    """CELL-14b: resolve the sender's display fields to denormalize onto the
    message row. Tries the write connection first (legacy single DB: members are
    co-located); falls back to the central DB (cell: a group's private DB has no
    members table)."""
    for use_central in (False, True):
        try:
            if use_central:
                import db as _db
                async with _db.global_db() as cdb:
                    m = await get_member(cdb, member_id)
            else:
                m = await get_member(write_db, member_id)
            if m:
                return (m["name"], m["type"], m["avatar_color"],
                        m.get("model_provider"), m.get("model_name"))
        except Exception:
            continue
    return (None, None, None, None, None)


async def save_message(db, group_id: int, member_id: int, content: str,
                       reply_to_id: int = None, file_url: str = None,
                       file_name: str = None, file_size: int = None,
                       file_type: str = None, is_auto_reply: bool = False,
                       input_tokens: int = None, output_tokens: int = None,
                       cache_read_tokens: int = None, cache_creation_tokens: int = None,
                       meta: dict = None):
    s_name, s_type, s_avatar, s_prov, s_model = await _sender_snapshot(db, member_id)
    meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
    async with db.execute(
        "INSERT INTO messages (group_id, member_id, content, reply_to_id, "
        "file_url, file_name, file_size, file_type, is_auto_reply, "
        "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
        "sender_name, sender_type, sender_avatar, sender_provider, sender_model, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (group_id, member_id, content, reply_to_id,
         file_url, file_name, file_size, file_type, int(is_auto_reply),
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
         s_name, s_type, s_avatar, s_prov, s_model, meta_json)
    ) as cur:
        await db.commit()
        return cur.lastrowid


async def update_member_setting(db, member_id: int, auto_reply: str | None):
    await db.execute("UPDATE members SET auto_reply=? WHERE id=?", (auto_reply or None, member_id))
    await db.commit()


async def clear_bot_context(db, member_id: int, group_id: int):
    from datetime import datetime
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    await db.execute("UPDATE members SET context_cleared_at=? WHERE id=?", (now, member_id))
    await db.commit()

    # Delete role_summaries (a GROUP table). Best-effort: a failure here must not
    # 500 the request after context_cleared_at is already committed — log and
    # continue, matching delete_bot_memory's graceful degradation below.
    try:
        # Single-db / test mode: the table lives on the current (central) connection.
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='role_summaries'"
        ) as cur:
            has_table = await cur.fetchone()

        if has_table:
            await db.execute("DELETE FROM role_summaries WHERE bot_id=? AND group_id=?", (member_id, group_id))
            await db.commit()
        else:
            # Split DB mode: the central connection has no role_summaries; delete
            # from the group's private DB.
            from runtime.dbpaths import group_db_path
            from db.writer import write_connect
            import os
            gpath = group_db_path(group_id)
            if os.path.exists(gpath):
                async with write_connect(gpath) as gdb:
                    await gdb.execute("DELETE FROM role_summaries WHERE bot_id=? AND group_id=?", (member_id, group_id))
                    await gdb.commit()
    except Exception:
        log.exception(
            "clear_bot_context: failed to delete role_summaries (bot_id=%s, group_id=%s)", member_id, group_id
        )

    # 删除经记忆组件的 forget() 接口，与读(recall)/写(observe)统一走同一 provider 抽象。
    # 删除是物理清理，恒用默认 Chroma 实现（不受 bot 的 memory=off 策略影响）。
    from ai.memory_provider import get_memory_provider
    await get_memory_provider().forget(member_id, group_id)


async def update_member_full(db, member_id: int, data: dict):
    executor_config = data.get('executor_config', {})
    if not isinstance(executor_config, str):
        executor_config = json.dumps(executor_config)
    traits = data.get('traits', [])
    traits_json = json.dumps(traits) if isinstance(traits, list) else traits

    await db.execute(
        "UPDATE members SET name=?, role=?, system_prompt=?, avatar_color=?, "
        "model_provider=?, model_name=?, temperature=?, max_tokens=?, "
        "personality_prompt=?, executor_id=?, executor_config=?, done_keyword=?, traits_json=? WHERE id=?",
        (data.get('name'), data.get('role'), data.get('system_prompt'),
         data.get('avatar_color'), data.get('model_provider'), data.get('model_name'),
         data.get('temperature', 0.7), data.get('max_tokens', 8192),
         data.get('personality_prompt') or None,
         data.get('executor_id', 'tool_loop_v1'), executor_config,
         data.get('done_keyword') or None, traits_json, member_id)
    )
    await db.commit()


async def update_message(db, msg_id: int, content: str,
                          input_tokens: int = None, output_tokens: int = None,
                          cache_read_tokens: int = None, cache_creation_tokens: int = None):
    await db.execute(
        "UPDATE messages SET content=?, edited_at=CURRENT_TIMESTAMP,"
        " input_tokens=COALESCE(?, input_tokens),"
        " output_tokens=COALESCE(?, output_tokens),"
        " cache_read_tokens=COALESCE(?, cache_read_tokens),"
        " cache_creation_tokens=COALESCE(?, cache_creation_tokens) WHERE id=?",
        (content, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, msg_id)
    )
    await db.commit()


async def soft_delete_message(db, msg_id: int):
    await db.execute("UPDATE messages SET is_deleted=1 WHERE id=?", (msg_id,))
    await db.commit()


async def save_compaction_summary(
    db, group_id: int, bot_id: int, summary: str, keep_ids: set[int]
) -> int:
    content = f"【历史摘要】\n{summary}"
    async with db.execute(
        "INSERT INTO messages (group_id, member_id, content) VALUES (?, ?, ?)",
        (group_id, bot_id, content)
    ) as cur:
        summary_id = cur.lastrowid
    await db.commit()

    if keep_ids:
        placeholders = ",".join("?" * len(keep_ids))
        await db.execute(
            f"UPDATE messages SET is_deleted=1 "
            f"WHERE group_id=? AND (is_deleted IS NULL OR is_deleted=0) "
            f"AND id NOT IN ({placeholders}) AND id != ?",
            (group_id, *sorted(keep_ids), summary_id)
        )
    else:
        await db.execute(
            "UPDATE messages SET is_deleted=1 "
            "WHERE group_id=? AND (is_deleted IS NULL OR is_deleted=0) AND id != ?",
            (group_id, summary_id)
        )
    await db.commit()
    return summary_id


async def get_message_meta(db, msg_id: int):
    async with db.execute(
        "SELECT id, group_id, member_id FROM messages WHERE id=?", (msg_id,)
    ) as cur:
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


async def get_group_assigned_worker(db, group_id: int) -> str:
    async with db.execute("SELECT assigned_worker_id FROM groups WHERE id = ?", (group_id,)) as cur:
        row = await cur.fetchone()
        return row[0] if row else 'w0'


async def increment_unread(db, group_id: int, member_id: int, delta: int = 1):
    await db.execute(
        """INSERT INTO unread_counts (group_id, member_id, unread, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(group_id, member_id) DO UPDATE SET
               unread = unread + excluded.unread,
               updated_at = datetime('now')""",
        (group_id, member_id, delta)
    )
    await db.commit()

async def get_unread_counts(db, member_id: int) -> dict[int, int]:
    async with db.execute("SELECT group_id, unread FROM unread_counts WHERE member_id = ?", (member_id,)) as cur:
        rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}


async def reset_unread(db, group_id: int, member_id: int):
    """Clear a member's unread for one group (they've read it)."""
    await db.execute(
        """INSERT INTO unread_counts (group_id, member_id, unread, updated_at)
           VALUES (?, ?, 0, datetime('now'))
           ON CONFLICT(group_id, member_id) DO UPDATE SET
               unread = 0, updated_at = datetime('now')""",
        (group_id, member_id),
    )
    await db.commit()


async def bump_unread_for_group(db, group_id: int, member_rows: list,
                                sender_id, online_ids) -> list[int]:
    """+1 unread for every HUMAN member of the group except the sender and anyone
    currently viewing this group (online_ids). Returns the member_ids bumped.
    Online members get a 'read' and reset to 0 anyway; skipping them avoids badge
    flicker. Bots never accrue unread."""
    bumped = []
    for m in member_rows:
        mid = m["id"]
        if m["type"] == "human" and mid != sender_id and mid not in online_ids:
            await increment_unread(db, group_id, mid, 1)
            bumped.append(mid)
    return bumped
