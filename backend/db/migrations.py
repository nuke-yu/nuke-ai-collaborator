"""
db/migrations.py — versioned schema migration runner.

Each migration_NNN function is idempotent: ALTER TABLE statements are wrapped
in try/except because SQLite has no ADD COLUMN IF NOT EXISTS syntax.

Adding a new migration:
  1. Write async def migration_NNN(db): ...
  2. Append it to MIGRATIONS
  That's it — run_migrations handles the rest on next startup.
"""

import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Migration functions
# ---------------------------------------------------------------------------

async def migration_001(db):
    """Add all columns introduced after the initial schema release."""
    stmts = [
        "ALTER TABLE messages ADD COLUMN reply_to_id INTEGER",
        "ALTER TABLE messages ADD COLUMN edited_at TIMESTAMP",
        "ALTER TABLE messages ADD COLUMN is_deleted INTEGER DEFAULT 0",
        "ALTER TABLE messages ADD COLUMN file_url TEXT",
        "ALTER TABLE messages ADD COLUMN file_name TEXT",
        "ALTER TABLE messages ADD COLUMN file_size INTEGER",
        "ALTER TABLE messages ADD COLUMN file_type TEXT",
        "ALTER TABLE messages ADD COLUMN is_auto_reply INTEGER DEFAULT 0",
        "ALTER TABLE members ADD COLUMN model_provider TEXT DEFAULT 'deepseek'",
        "ALTER TABLE members ADD COLUMN model_name TEXT DEFAULT 'deepseek-chat'",
        "ALTER TABLE members ADD COLUMN auto_reply TEXT DEFAULT NULL",
        "ALTER TABLE members ADD COLUMN context_cleared_at TEXT DEFAULT NULL",
        "ALTER TABLE members ADD COLUMN temperature REAL DEFAULT 0.7",
        "ALTER TABLE members ADD COLUMN max_tokens INTEGER DEFAULT 4096",
        "ALTER TABLE members ADD COLUMN personality_prompt TEXT DEFAULT NULL",
        "ALTER TABLE members ADD COLUMN executor_id TEXT DEFAULT 'simple_v1'",
        "ALTER TABLE members ADD COLUMN executor_config TEXT DEFAULT '{}'",
        "ALTER TABLE members ADD COLUMN done_keyword TEXT DEFAULT NULL",
        "ALTER TABLE groups ADD COLUMN announcement TEXT DEFAULT NULL",
        "ALTER TABLE role_summaries ADD COLUMN bot_id INTEGER DEFAULT NULL",
    ]
    for sql in stmts:
        try:
            await db.execute(sql)
        except Exception:
            pass  # column already exists on new DBs or repeated runs
    await db.commit()


# ---------------------------------------------------------------------------
# Registry — append new migrations here, never reorder or remove existing ones
# ---------------------------------------------------------------------------

MIGRATIONS: list = [
    migration_001,
]

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_migrations(db) -> None:
    """Create the version table if absent, then apply any pending migrations."""
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

    pending = [(i + 1, fn) for i, fn in enumerate(MIGRATIONS) if i + 1 > current]
    if not pending:
        return

    for version, migration_fn in pending:
        log.info("applying DB migration %03d: %s", version, migration_fn.__name__)
        await migration_fn(db)
        await db.execute(
            "INSERT INTO _schema_version (version) VALUES (?)", (version,)
        )
        await db.commit()
        log.info("migration %03d applied", version)
