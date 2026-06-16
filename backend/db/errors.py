"""Shared classification of SQLite errors."""
import sqlite3


def is_missing_schema_error(e: BaseException) -> bool:
    """True if `e` is a SQLite "no such column/table" error — i.e. a migration gap.

    Such errors must surface loudly (re-raise / distinct log) rather than be swallowed
    as "no data": a swallowed missing-column silently degrades a feature and hides the
    real deploy bug (a group DB not brought to the current schema). See ensure_group_ready.
    """
    if not isinstance(e, sqlite3.OperationalError):
        return False
    msg = str(e).lower()
    return "no such column" in msg or "no such table" in msg
