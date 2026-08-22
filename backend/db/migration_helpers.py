"""Idempotent schema mutation helpers shared by migrations."""
from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)


async def safe_add_column(db, sql: str) -> None:
    try:
        parts = sql.split()
        if len(parts) >= 3 and parts[0].upper() == "ALTER" and parts[1].upper() == "TABLE":
            table_name = parts[2].strip("`\"[]")
            from db.schema_split import CENTRAL_TABLES, GROUP_TABLES
            if table_name in CENTRAL_TABLES or table_name in GROUP_TABLES:
                cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                if (await cur.fetchone()) is None:
                    log.debug("Skipping column migration for non-existent split table %s: %s", table_name, sql)
                    return
    except Exception as exc:
        log.warning("failed to check table existence for %s: %s", sql, exc)
    try:
        await db.execute(sql)
    except sqlite3.OperationalError as exc:
        if "duplicate column" in str(exc).lower():
            log.debug("column already present, skipping: %s", sql)
            return
        raise
