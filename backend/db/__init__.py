import aiosqlite
import os
from contextlib import asynccontextmanager, contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")


@asynccontextmanager
async def connect(path: str | None = None):
    # DFT-028/029: single connect helper. WAL + busy_timeout avoid
    # "database is locked" under concurrent writers; foreign_keys=ON makes
    # SQLite actually enforce the FK constraints (it ignores them by default).
    conn = await aiosqlite.connect(path if path is not None else DB_PATH)
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        await conn.close()


@contextmanager
def connect_sync(path: str | None = None):
    """Synchronous connection for low-frequency lookups (e.g. workspace redirection)."""
    import sqlite3
    conn = sqlite3.connect(path if path is not None else DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def get_db():
    return connect()


from db.writer import write_connect, aclose_writer  # noqa: E402


from db.schema import init_db  # noqa: E402
from db.queries import (       # noqa: E402
    get_group, get_members, get_member,
    get_messages, get_all_messages, get_member_stats,
    save_message, update_member_setting, clear_bot_context,
    update_member_full, update_message, soft_delete_message,
    save_compaction_summary, get_message_meta,
    toggle_reaction, get_reactions_for_message, get_reactions_for_group,
    pin_message, unpin_message, get_pinned_messages,
)

__all__ = [
    "DB_PATH", "get_db", "connect", "connect_sync", "write_connect", "aclose_writer", "init_db",
    "get_group", "get_members", "get_member",
    "get_messages", "get_all_messages", "get_member_stats",
    "save_message", "update_member_setting", "clear_bot_context",
    "update_member_full", "update_message", "soft_delete_message",
    "save_compaction_summary", "get_message_meta",
    "toggle_reaction", "get_reactions_for_message", "get_reactions_for_group",
    "pin_message", "unpin_message", "get_pinned_messages",
]
