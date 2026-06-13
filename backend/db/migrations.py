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
import sqlite3

log = logging.getLogger(__name__)


async def _safe_add_column(db, sql: str) -> None:
    """Run an idempotent ADD COLUMN, swallowing ONLY the benign 'duplicate
    column' case, and skipping if the table does not exist (split/legacy DBs).
    """
    try:
        parts = sql.split()
        if len(parts) >= 3 and parts[0].upper() == "ALTER" and parts[1].upper() == "TABLE":
            table_name = parts[2].strip("`\"[]")
            from db.schema_split import CENTRAL_TABLES, GROUP_TABLES
            
            # Check if the table is a known split table and exists in this database
            if table_name in CENTRAL_TABLES or table_name in GROUP_TABLES:
                cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                if (await cur.fetchone()) is None:
                    log.debug("Skipping column migration for non-existent split table %s: %s", table_name, sql)
                    return
    except Exception as e:
        log.warning("failed to check table existence for %s: %s", sql, e)

    try:
        await db.execute(sql)
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            log.debug("column already present, skipping: %s", sql)
            return
        raise


# ---------------------------------------------------------------------------
# Migration functions
# ---------------------------------------------------------------------------

async def migration_001(db):
    """Add all columns introduced after the initial schema release.

    Rollback (SQLite >= 3.35):
        ALTER TABLE messages DROP COLUMN reply_to_id;
        ALTER TABLE messages DROP COLUMN edited_at;
        ALTER TABLE messages DROP COLUMN is_deleted;
        ALTER TABLE messages DROP COLUMN file_url;
        ALTER TABLE messages DROP COLUMN file_name;
        ALTER TABLE messages DROP COLUMN file_size;
        ALTER TABLE messages DROP COLUMN file_type;
        ALTER TABLE messages DROP COLUMN is_auto_reply;
        ALTER TABLE members DROP COLUMN model_provider;
        ALTER TABLE members DROP COLUMN model_name;
        ALTER TABLE members DROP COLUMN auto_reply;
        ALTER TABLE members DROP COLUMN context_cleared_at;
        ALTER TABLE members DROP COLUMN temperature;
        ALTER TABLE members DROP COLUMN max_tokens;
        ALTER TABLE members DROP COLUMN personality_prompt;
        ALTER TABLE members DROP COLUMN executor_id;
        ALTER TABLE members DROP COLUMN executor_config;
        ALTER TABLE members DROP COLUMN done_keyword;
        ALTER TABLE groups DROP COLUMN announcement;
        ALTER TABLE role_summaries DROP COLUMN bot_id;
    """
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
        "ALTER TABLE members ADD COLUMN max_tokens INTEGER DEFAULT 8192",
        "ALTER TABLE members ADD COLUMN personality_prompt TEXT DEFAULT NULL",
        "ALTER TABLE members ADD COLUMN executor_id TEXT DEFAULT 'tool_loop_v1'",
        "ALTER TABLE members ADD COLUMN executor_config TEXT DEFAULT '{}'",
        "ALTER TABLE members ADD COLUMN done_keyword TEXT DEFAULT NULL",
        "ALTER TABLE groups ADD COLUMN announcement TEXT DEFAULT NULL",
        "ALTER TABLE role_summaries ADD COLUMN bot_id INTEGER DEFAULT NULL",
    ]
    for sql in stmts:
        await _safe_add_column(db, sql)
    await db.commit()


async def migration_002(db):
    """Add token usage columns to messages table.

    Rollback:
        ALTER TABLE messages DROP COLUMN input_tokens;
        ALTER TABLE messages DROP COLUMN output_tokens;
    """
    stmts = [
        "ALTER TABLE messages ADD COLUMN input_tokens INTEGER DEFAULT NULL",
        "ALTER TABLE messages ADD COLUMN output_tokens INTEGER DEFAULT NULL",
    ]
    for sql in stmts:
        await _safe_add_column(db, sql)
    await db.commit()


async def migration_003(db):
    """Add cron_jobs table for the scheduler plugin.

    Rollback:
        DROP TABLE IF EXISTS cron_jobs;
    """
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
    """Add agent_sessions and session_events tables for crash-safe session recovery.

    Rollback:
        DROP INDEX IF EXISTS idx_session_events;
        DROP TABLE IF EXISTS session_events;
        DROP TABLE IF EXISTS agent_sessions;
    """
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
    """Add cache token columns to messages table.

    Rollback:
        ALTER TABLE messages DROP COLUMN cache_read_tokens;
        ALTER TABLE messages DROP COLUMN cache_creation_tokens;
    """
    stmts = [
        "ALTER TABLE messages ADD COLUMN cache_read_tokens INTEGER DEFAULT NULL",
        "ALTER TABLE messages ADD COLUMN cache_creation_tokens INTEGER DEFAULT NULL",
    ]
    for sql in stmts:
        await _safe_add_column(db, sql)
    await db.commit()


async def migration_006(db):
    """Add cache token columns to agent_sessions for session-level aggregation.

    Rollback:
        ALTER TABLE agent_sessions DROP COLUMN cache_read_tokens;
        ALTER TABLE agent_sessions DROP COLUMN cache_creation_tokens;
    """
    stmts = [
        "ALTER TABLE agent_sessions ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE agent_sessions ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0",
    ]
    for sql in stmts:
        await _safe_add_column(db, sql)
    await db.commit()


async def migration_007(db):
    """Add workflow_state table for crash-safe workflow/orchestration recovery.

    The orchestrator (DeclarativeOrchestrator) holds workflow progress in an
    in-memory dict; this table is its durable snapshot (one row per group),
    overwritten whenever the workflow state changes and cleared on completion.

    Rollback:
        DROP TABLE IF EXISTS workflow_state;
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
    """Add last_snapshot_json column to agent_sessions for full context snapshots.

    Rollback:
        ALTER TABLE agent_sessions DROP COLUMN last_snapshot_json;
    """
    await _safe_add_column(db, "ALTER TABLE agent_sessions ADD COLUMN last_snapshot_json TEXT")
    await db.commit()


async def migration_009(db):
    """Create tickets table for persistent task tracking and archiving.

    Rollback:
        DROP TABLE IF EXISTS tickets;
    """
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
    """Create group_locks table to persist the active bot state.

    Rollback:
        DROP TABLE IF EXISTS group_locks;
    """
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


async def migration_011(db):
    """Add traits_json column to members table for atomic skill composition.

    Rollback:
        ALTER TABLE members DROP COLUMN traits_json;
    """
    await _safe_add_column(db, "ALTER TABLE members ADD COLUMN traits_json TEXT DEFAULT '[]'")
    await db.commit()


async def migration_012(db):
    """Add total_usd_cost column to tickets table for token cost tracking.

    Rollback:
        ALTER TABLE tickets DROP COLUMN total_usd_cost;
    """
    await _safe_add_column(db, "ALTER TABLE tickets ADD COLUMN total_usd_cost REAL DEFAULT 0.0")
    await db.commit()


async def migration_013(db):
    """Add last_run_at column to cron_jobs for misfire detection and catch-up.

    Rollback:
        ALTER TABLE cron_jobs DROP COLUMN last_run_at;
    """
    try:
        await db.execute("ALTER TABLE cron_jobs ADD COLUMN last_run_at TIMESTAMP")
    except Exception:
        pass
    await db.commit()


async def migration_014(db):
    """CELL-14b: denormalize the sender's display fields onto each message so the
    messages query needs no cross-domain JOIN to the central `members` table
    (group private DBs are self-contained). Backfill existing rows from members.

    Rollback:
        ALTER TABLE messages DROP COLUMN sender_name;
        ALTER TABLE messages DROP COLUMN sender_type;
        ALTER TABLE messages DROP COLUMN sender_avatar;
        ALTER TABLE messages DROP COLUMN sender_provider;
        ALTER TABLE messages DROP COLUMN sender_model;
        -- Note: the backfill UPDATE is non-reversible; member display data
        -- may have changed since the migration ran.
    """
    for col in ("sender_name TEXT", "sender_type TEXT", "sender_avatar TEXT",
                "sender_provider TEXT", "sender_model TEXT"):
        await _safe_add_column(db, f"ALTER TABLE messages ADD COLUMN {col}")
    await db.execute("""
        UPDATE messages SET
            sender_name     = (SELECT name           FROM members WHERE id = messages.member_id),
            sender_type     = (SELECT type           FROM members WHERE id = messages.member_id),
            sender_avatar   = (SELECT avatar_color    FROM members WHERE id = messages.member_id),
            sender_provider = (SELECT model_provider  FROM members WHERE id = messages.member_id),
            sender_model    = (SELECT model_name      FROM members WHERE id = messages.member_id)
        WHERE sender_name IS NULL
    """)
    await db.commit()



async def migration_015(db):
    """CELL-15: Add assigned_worker_id to groups table for persistent routing.

    Rollback:
        ALTER TABLE groups DROP COLUMN assigned_worker_id;
    """
    await _safe_add_column(db, "ALTER TABLE groups ADD COLUMN assigned_worker_id TEXT DEFAULT 'w0'")
    await db.commit()


async def migration_016(db):
    """Add meta (JSON) to messages — carries structured payloads like the
    workflow human-confirmation gate card (meta.kind = 'confirm_gate').

    Rollback:
        ALTER TABLE messages DROP COLUMN meta;
    """
    await _safe_add_column(db, "ALTER TABLE messages ADD COLUMN meta TEXT DEFAULT NULL")
    await db.commit()


async def migration_017(db):
    """Add away_summary column to groups table to cache pre-generated recap.

    Rollback:
        ALTER TABLE groups DROP COLUMN away_summary;
    """
    await _safe_add_column(db, "ALTER TABLE groups ADD COLUMN away_summary TEXT DEFAULT NULL")
    await db.commit()


async def migration_018(db):
    """Add missing indexes for query-hot columns (DFT-007/017).

    messages(group_id, created_at) covers the common paginated history fetch.
    messages(group_id, member_id) covers per-member message queries.
    role_summaries(bot_id, group_id) covers per-bot summary lookup.
    agent_sessions(status, updated_at) covers active-session polling.
    All are CREATE INDEX IF NOT EXISTS so re-running is idempotent.

    Rollback:
        DROP INDEX IF EXISTS idx_messages_group_created;
        DROP INDEX IF EXISTS idx_messages_group_member;
        DROP INDEX IF EXISTS idx_role_summaries_bot_group;
        DROP INDEX IF EXISTS idx_agent_sessions_status;
    """
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_messages_group_created ON messages(group_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_messages_group_member ON messages(group_id, member_id)",
        "CREATE INDEX IF NOT EXISTS idx_role_summaries_bot_group ON role_summaries(bot_id, group_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status, updated_at)",
    ]
    for stmt in stmts:
        try:
            await db.execute(stmt)
        except Exception:
            pass  # table or column absent in this DB (e.g. central DB schema)
    await db.commit()


async def migration_019(db):
    """Add project column to tickets table for cross-project board visibility.

    Rollback:
        ALTER TABLE tickets DROP COLUMN project;
    """
    await _safe_add_column(db, "ALTER TABLE tickets ADD COLUMN project TEXT DEFAULT ''")
    await db.commit()


async def migration_020(db):
    """Create reflection_state table: per-(bot, group) consolidation watermark (P1 巩固层).

    Stores the timestamp of the newest fact already reflected over, so reflection
    only consolidates new facts. Group-DB table; harmless CREATE on the central DB.

    Rollback:
        DROP TABLE IF EXISTS reflection_state;
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS reflection_state (
            bot_id             INTEGER NOT NULL,
            group_id           INTEGER NOT NULL,
            covered_through_ts REAL    NOT NULL DEFAULT 0,
            updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (bot_id, group_id)
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
    migration_011,
    migration_012,
    migration_013,
    migration_014,
    migration_015,
    migration_016,
    migration_017,
    migration_018,
    migration_019,
    migration_020,
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
