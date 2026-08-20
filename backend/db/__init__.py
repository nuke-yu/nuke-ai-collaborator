import aiosqlite
import os
from contextlib import asynccontextmanager, contextmanager

from db.context import resolve as _route, bind_db, current_db_path
from db.adapters import selected_external_adapter

DB_PATH = os.environ.get("NUKE_DB_PATH") or os.path.join(os.path.dirname(__file__), "chat.db")


@asynccontextmanager
async def connect(path: str | None = None):
    adapter = selected_external_adapter()
    if adapter is not None:
        async with adapter.connect(_route(path, DB_PATH)) as conn:
            yield conn
        return
    # DFT-028/029: single connect helper. WAL + busy_timeout avoid
    # "database is locked" under concurrent writers; foreign_keys=ON makes
    # SQLite actually enforce the FK constraints (it ignores them by default).
    # DFT-066: aiosqlite runs each connection on its own OS thread. If the
    # `async with` is abandoned (e.g. a fire-and-forget task cancelled at loop
    # teardown), the finally below never runs and a NON-daemon thread lingers,
    # blocking process exit and hanging the test/run. Mark the thread daemon
    # before it starts so an orphaned read connection can never block exit.
    conn = aiosqlite.connect(_route(path, DB_PATH))
    conn.daemon = True
    conn = await conn
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
    adapter = selected_external_adapter()
    if adapter is not None:
        with adapter.connect_sync(_route(path, DB_PATH)) as conn:
            yield conn
        return
    import sqlite3
    conn = sqlite3.connect(_route(path, DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()


def get_db():
    """Read connection for the CURRENTLY BOUND group DB (CELL-04), else DB_PATH."""
    return connect()


def global_db():
    """Read connection to the CENTRAL DB, bypassing any bound group context
    (CELL-04). Use for global-domain tables: groups / members / app_config /
    templates / unread_counts."""
    return connect(DB_PATH)


from db.writer import write_connect, aclose_writer, stats as writer_stats  # noqa: E402


from db.schema import init_db  # noqa: E402
from db.schema_split import (   # noqa: E402  CELL-05: central + per-group split inits
    init_central_db, init_group_db, CENTRAL_TABLES, GROUP_TABLES,
    ensure_group_db_ready,
)
from db.queries import (       # noqa: E402
    get_group, get_members, get_member,
    get_messages, get_all_messages, get_member_stats,
    save_message, update_member_setting, clear_bot_context,
    update_member_full, update_message, soft_delete_message,
    save_compaction_summary, get_message_meta,
    toggle_reaction, get_reactions_for_message, get_reactions_for_group,
    pin_message, unpin_message, get_pinned_messages,
    increment_unread, get_unread_counts, reset_unread, bump_unread_for_group,
)

__all__ = [
    "DB_PATH", "get_db", "global_db", "connect", "connect_sync", "write_connect", "aclose_writer", "writer_stats",
    "bind_db", "current_db_path", "init_db",
    "init_central_db", "init_group_db", "CENTRAL_TABLES", "GROUP_TABLES",
    "ensure_group_db_ready",
    "get_group", "get_members", "get_member",
    "get_messages", "get_all_messages", "get_member_stats",
    "save_message", "update_member_setting", "clear_bot_context",
    "update_member_full", "update_message", "soft_delete_message",
    "save_compaction_summary", "get_message_meta",
    "toggle_reaction", "get_reactions_for_message", "get_reactions_for_group",
    "pin_message", "unpin_message", "get_pinned_messages",
    "increment_unread", "get_unread_counts", "reset_unread", "bump_unread_for_group",
]
