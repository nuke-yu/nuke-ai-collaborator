import asyncio
import logging
from db import connect

log = logging.getLogger(__name__)

async def get_active_bot(group_id: int) -> int | None:
    """Retrieve the bot_id currently locking this group from SQLite."""
    try:
        async with connect() as db:
            async with db.execute("SELECT bot_id FROM group_locks WHERE group_id = ?", (group_id,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else None
    except Exception:
        log.exception("Failed to get active bot for group %d", group_id)
        return None

async def set_active_bot(group_id: int, bot_id: int) -> None:
    """Persist the active bot lock to SQLite (Upsert)."""
    try:
        async with connect() as db:
            await db.execute("""
                INSERT INTO group_locks (group_id, bot_id, locked_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(group_id) DO UPDATE SET
                    bot_id = excluded.bot_id,
                    locked_at = datetime('now')
            """, (group_id, bot_id))
            await db.commit()
    except Exception:
        log.exception("Failed to set active bot %d for group %d", bot_id, group_id)

async def release_lock(group_id: int) -> None:
    """Remove the active bot lock from SQLite."""
    try:
        async with connect() as db:
            await db.execute("DELETE FROM group_locks WHERE group_id = ?", (group_id,))
            await db.commit()
    except Exception:
        log.exception("Failed to release lock for group %d", group_id)
