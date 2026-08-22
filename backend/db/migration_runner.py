"""Generic, ordered SQLite migration runner."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def run_migrations(db, migrations: list) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS _schema_version (
            version    INTEGER NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.commit()
    cur = await db.execute("SELECT MAX(version) FROM _schema_version")
    row = await cur.fetchone()
    current = row[0] or 0
    for version, migration_fn in ((i + 1, fn) for i, fn in enumerate(migrations) if i + 1 > current):
        log.info("applying DB migration %03d: %s", version, migration_fn.__name__)
        await migration_fn(db)
        await db.execute("INSERT INTO _schema_version (version) VALUES (?)", (version,))
        await db.commit()
        log.info("migration %03d applied", version)
