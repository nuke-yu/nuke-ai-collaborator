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


async def migration_002(db):
    """Add token usage columns to messages table."""
    stmts = [
        "ALTER TABLE messages ADD COLUMN input_tokens INTEGER DEFAULT NULL",
        "ALTER TABLE messages ADD COLUMN output_tokens INTEGER DEFAULT NULL",
    ]
    for sql in stmts:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()


async def migration_003(db):
    """Add cron_jobs table for the scheduler plugin."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS cron_jobs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id     INTEGER NOT NULL,
            group_id   INTEGER NOT NULL,
            cron_expr  TEXT    NOT NULL,
            message    TEXT    NOT NULL,
            label      TEXT    DEFAULT '',
            enabled    INTEGER DEFAULT 1,
            created_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (bot_id)   REFERENCES members(id),
            FOREIGN KEY (group_id) REFERENCES groups(id)
        )
    """)
    await db.commit()


# ---------------------------------------------------------------------------
# Registry — append new migrations here, never reorder or remove existing ones
# ---------------------------------------------------------------------------

async def migration_004(db):
    """Add agent_sessions and session_events tables for crash-safe session recovery."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id            TEXT PRIMARY KEY,
            parent_id     TEXT DEFAULT NULL,
            bot_id        INTEGER NOT NULL,
            group_id      INTEGER NOT NULL,
            status        TEXT NOT NULL DEFAULT 'running',
            executor_id   TEXT NOT NULL DEFAULT 'tool_loop_v1',
            config_json   TEXT NOT NULL DEFAULT '{}',
            user_message  TEXT NOT NULL DEFAULT '',
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (bot_id)   REFERENCES members(id),
            FOREIGN KEY (group_id) REFERENCES groups(id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_events ON session_events(session_id, id)"
    )
    await db.commit()


async def migration_005(db):
    """Add cache token columns to messages table."""
    stmts = [
        "ALTER TABLE messages ADD COLUMN cache_read_tokens INTEGER DEFAULT NULL",
        "ALTER TABLE messages ADD COLUMN cache_creation_tokens INTEGER DEFAULT NULL",
    ]
    for sql in stmts:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()


async def migration_006(db):
    """Add cache token columns to agent_sessions for session-level aggregation."""
    stmts = [
        "ALTER TABLE agent_sessions ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE agent_sessions ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0",
    ]
    for sql in stmts:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()


async def migration_007(db):
    """Add workflow_state table for crash-safe workflow/orchestration recovery.

    The orchestrator (DeclarativeOrchestrator) holds workflow progress in an
    in-memory dict; this table is its durable snapshot (one row per group),
    overwritten whenever the workflow state changes and cleared on completion.
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workflow_state (
            group_id        INTEGER PRIMARY KEY,
            orchestrator_id TEXT NOT NULL DEFAULT 'workflow_v1',
            state_json      TEXT NOT NULL DEFAULT '{}',
            status          TEXT NOT NULL DEFAULT 'active',
            updated_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (group_id) REFERENCES groups(id)
        )
    """)
    await db.commit()


async def migration_008(db):
    """Add last_snapshot_json column to agent_sessions for full context snapshots."""
    try:
        await db.execute("ALTER TABLE agent_sessions ADD COLUMN last_snapshot_json TEXT")
    except Exception:
        pass
    await db.commit()


async def migration_009(db):
    """Create tickets table for persistent task tracking and archiving."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            group_id INTEGER NOT NULL,
            title TEXT,
            status TEXT DEFAULT 'backlog', -- backlog, in_progress, done
            assignee_id INTEGER,
            priority TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            UNIQUE(group_id, ticket_id)
        )
    """)
    await db.commit()


async def migration_010(db):
    """Create group_locks table to persist the active bot state."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS group_locks (
            group_id INTEGER PRIMARY KEY,
            bot_id INTEGER NOT NULL,
            locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups(id),
            FOREIGN KEY (bot_id) REFERENCES members(id)
        )
    """)
    await db.commit()


MIGRATIONS: list = [
    migration_001,
    migration_002,
    migration_003,
    migration_004,
    migration_005,
    migration_006,
    migration_007,
    migration_008,
    migration_009,
    migration_010,
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
