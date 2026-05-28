import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")


def get_db():
    return aiosqlite.connect(DB_PATH)


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
    "DB_PATH", "get_db", "init_db",
    "get_group", "get_members", "get_member",
    "get_messages", "get_all_messages", "get_member_stats",
    "save_message", "update_member_setting", "clear_bot_context",
    "update_member_full", "update_message", "soft_delete_message",
    "save_compaction_summary", "get_message_meta",
    "toggle_reaction", "get_reactions_for_message", "get_reactions_for_group",
    "pin_message", "unpin_message", "get_pinned_messages",
]
