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


async def migration_021(db):
    """Add per-user recap acknowledgment watermark to member_read. When a user
    dismisses (✕) the away recap, last_recap_ack_id advances to the latest message
    so that batch never re-shows for THAT user (survives reconnect / group switch),
    while genuinely new activity past the watermark still surfaces a fresh recap.
    Per-user; one member's ✕ never clears it for teammates. Group-DB table; harmless
    no-op on the central DB (member_read absent there → _safe_add_column skips).

    Rollback:
        ALTER TABLE member_read DROP COLUMN last_recap_ack_id;
    """
    await _safe_add_column(db, "ALTER TABLE member_read ADD COLUMN last_recap_ack_id INTEGER NOT NULL DEFAULT 0")
    await db.commit()


async def migration_022(db):
    """Scope role summaries to the human-given discussion topic (thread). Summaries
    written during a discussion are stamped with that discussion's thread_id; recall
    only injects summaries for the active topic, so an unrelated topic (e.g. a prior
    stock debate) no longer bleeds into a new discussion. Legacy rows keep thread_id
    NULL and are treated as un-scoped (never force-injected). Group-DB table; harmless
    no-op on the central DB (role_summaries absent there → _safe_add_column skips).

    Rollback:
        ALTER TABLE role_summaries DROP COLUMN thread_id;
    """
    await _safe_add_column(db, "ALTER TABLE role_summaries ADD COLUMN thread_id TEXT DEFAULT NULL")
    await db.commit()


async def migration_023(db):
    """Upgrade reflection_state's PRIMARY KEY to (bot_id, group_id, thread_id) for
    per-thread reflection watermarks. SQLite can't ALTER a primary key, so the table
    is rebuilt (RENAME → CREATE → COPY → DROP).

    The rebuild runs inside ONE explicit transaction so an interrupted run rolls back
    atomically — under aiosqlite's legacy isolation each bare DDL would otherwise
    auto-commit, leaving a half-migrated DB (e.g. orphan reflection_state_old, missing
    reflection_state). The guards are also recovery-aware: if a prior (non-atomic) run
    crashed mid-way and left reflection_state_old behind, it is used as the data source
    rather than mistaken for "nothing to do".

    Rollback:
        recreate reflection_state with PRIMARY KEY (bot_id, group_id), drop thread_id.
    """
    async def _has(name: str) -> bool:
        cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return (await cur.fetchone()) is not None

    async def _is_upgraded() -> bool:
        cur = await db.execute("PRAGMA table_info(reflection_state)")
        return "thread_id" in [row[1] for row in await cur.fetchall()]

    has_new = await _has("reflection_state")
    has_old = await _has("reflection_state_old")

    if not has_new and not has_old:
        return  # central/legacy DB without this table → nothing to migrate
    if has_new and not has_old and await _is_upgraded():
        return  # already upgraded and clean → idempotent no-op

    # Close any stray transaction so the explicit BEGIN below can't fail with
    # "cannot start a transaction within a transaction", then rebuild atomically.
    await db.commit()
    try:
        await db.execute("BEGIN")
        if has_new and await _is_upgraded():
            # Crashed after CREATE, before DROP: new table is good, only a stale leftover remains.
            await db.execute("DROP TABLE reflection_state_old")
            await db.execute("COMMIT")
            return
        if has_new:
            # reflection_state is the pre-upgrade table → set it aside as the data source,
            # discarding any stale leftover first so the RENAME can't collide.
            if has_old:
                await db.execute("DROP TABLE reflection_state_old")
            await db.execute("ALTER TABLE reflection_state RENAME TO reflection_state_old")
        # else: only reflection_state_old exists (crash after RENAME) → it already holds the source.

        await db.execute("""
            CREATE TABLE reflection_state (
                bot_id             INTEGER NOT NULL,
                group_id           INTEGER NOT NULL,
                thread_id          TEXT    NOT NULL DEFAULT '',
                covered_through_ts REAL    NOT NULL DEFAULT 0,
                updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (bot_id, group_id, thread_id)
            )
        """)
        await db.execute("""
            INSERT INTO reflection_state (bot_id, group_id, thread_id, covered_through_ts, updated_at)
            SELECT bot_id, group_id, '', covered_through_ts, updated_at FROM reflection_state_old
        """)
        await db.execute("DROP TABLE reflection_state_old")
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise


async def migration_024(db):
    """Plan A: create bot_skills (capability) + external_skills (import registry).

    Both are central-domain tables. CREATE IF NOT EXISTS is idempotent and
    harmless if it also runs on a group DB (the tables stay empty there).

    Rollback:
        DROP TABLE IF EXISTS bot_skills;
        DROP TABLE IF EXISTS external_skills;
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_skills (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id      INTEGER NOT NULL,
            skill_name  TEXT    NOT NULL,
            pool        TEXT    NOT NULL DEFAULT 'external_global',
            enabled     INTEGER NOT NULL DEFAULT 1,
            assigned_by INTEGER,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_id, skill_name),
            FOREIGN KEY (bot_id) REFERENCES members(id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS external_skills (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            scope_kind  TEXT    NOT NULL DEFAULT 'global',
            group_id    INTEGER NOT NULL DEFAULT 0,
            source_url  TEXT,
            ref         TEXT,
            commit_sha  TEXT,
            version     TEXT,
            platforms   TEXT,
            high_privilege TEXT,
            imported_by INTEGER,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status      TEXT    NOT NULL DEFAULT 'active',
            UNIQUE(scope_kind, group_id, name)
        )
    """)
    await db.commit()


async def migration_025(db):
    """L1 — tool_events: deterministic per-tool-call event log (group-domain).

    CREATE IF NOT EXISTS is idempotent and harmless if it also runs on the
    central DB (the table stays empty there). See db/schema_split.py _GROUP_DDL
    for the canonical shape applied to fresh group DBs.

    Rollback:
        DROP INDEX IF EXISTS idx_tool_events_grp_ts;
        DROP TABLE IF EXISTS tool_events;
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tool_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             INTEGER NOT NULL,
            group_id       INTEGER NOT NULL,
            bot_id         INTEGER,
            thread_id      TEXT    NOT NULL DEFAULT '',
            tool           TEXT    NOT NULL,
            args_summary   TEXT    NOT NULL DEFAULT '',
            result_summary TEXT    NOT NULL DEFAULT '',
            is_error       INTEGER NOT NULL DEFAULT 0,
            files_touched  TEXT    NOT NULL DEFAULT '[]',
            command        TEXT
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_events_grp_ts ON tool_events(group_id, ts)"
    )
    await db.commit()


async def migration_026(db):
    """L4 — tool_events.compressed flag + uncompressed lookup index.

    ADD COLUMN is idempotent via _safe_add_column (swallows duplicate-column,
    skips if the table doesn't exist on this split DB). CREATE INDEX IF NOT
    EXISTS is harmless on the central DB where tool_events stays empty.

    Rollback:
        DROP INDEX IF EXISTS idx_tool_events_uncompressed;
        -- SQLite has no DROP COLUMN before 3.35; leaving the column is benign.
    """
    await _safe_add_column(
        db, "ALTER TABLE tool_events ADD COLUMN compressed INTEGER NOT NULL DEFAULT 0"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_events_uncompressed "
        "ON tool_events(group_id, bot_id, compressed)"
    )
    await db.commit()


async def migration_027(db):
    """L3 upgrade — FTS5 ranked search over tool_events.

    Creates an external-content FTS5 virtual table mirroring tool_events' text
    columns (no data duplication; the index references rows by rowid=id), keeps
    it in sync with AFTER INSERT/DELETE triggers, and backfills existing rows via
    the 'rebuild' command. search_events uses MATCH + bm25() when this exists and
    falls back to LIKE otherwise — so if a SQLite build lacks FTS5 this migration
    degrades to a no-op (wrapped in try/except) rather than blocking startup.

    No UPDATE trigger: a row's searchable columns are immutable after insert
    (only the `compressed` flag changes, which isn't indexed).

    Rollback:
        DROP TRIGGER IF EXISTS tool_events_fts_ai;
        DROP TRIGGER IF EXISTS tool_events_fts_ad;
        DROP TABLE IF EXISTS tool_events_fts;
    """
    # Only meaningful where tool_events exists (group DBs + the empty central copy).
    cur = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_events'"
    )
    if (await cur.fetchone()) is None:
        return
    try:
        await db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS tool_events_fts USING fts5("
            "tool, args_summary, result_summary, command, files_touched, "
            "content='tool_events', content_rowid='id')"
        )
    except Exception as e:
        log.warning("migration_027: FTS5 unavailable, skipping tool_events_fts (search falls back to LIKE): %s", e)
        return
    await db.execute(
        "CREATE TRIGGER IF NOT EXISTS tool_events_fts_ai AFTER INSERT ON tool_events BEGIN "
        "INSERT INTO tool_events_fts(rowid, tool, args_summary, result_summary, command, files_touched) "
        "VALUES (new.id, new.tool, new.args_summary, new.result_summary, new.command, new.files_touched); END"
    )
    await db.execute(
        "CREATE TRIGGER IF NOT EXISTS tool_events_fts_ad AFTER DELETE ON tool_events BEGIN "
        "INSERT INTO tool_events_fts(tool_events_fts, rowid, tool, args_summary, result_summary, command, files_touched) "
        "VALUES ('delete', old.id, old.tool, old.args_summary, old.result_summary, old.command, old.files_touched); END"
    )
    # Backfill rows that predate the index.
    await db.execute("INSERT INTO tool_events_fts(tool_events_fts) VALUES('rebuild')")
    await db.commit()


async def migration_028(db):
    """P1-1: Create agent_tasks table for persistent coding agent task state.

    Stores the full lifecycle of coding agent tasks: creation, execution,
    completion/failure, PR creation. Replaces in-memory _tasks dict with
    durable storage that survives process restarts.

    Rollback:
        DROP INDEX IF EXISTS idx_agent_tasks_group_status;
        DROP TABLE IF EXISTS agent_tasks;
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_tasks (
            task_id        TEXT PRIMARY KEY,
            group_id       INTEGER NOT NULL,
            bot_id         INTEGER NOT NULL,
            repo_url       TEXT NOT NULL,
            requirements   TEXT NOT NULL,
            base_branch    TEXT NOT NULL DEFAULT 'main',
            test_command   TEXT NOT NULL DEFAULT '',
            model          TEXT NOT NULL DEFAULT 'deepseek-chat',
            max_iterations INTEGER NOT NULL DEFAULT 100,
            status         TEXT NOT NULL DEFAULT 'created',
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            pr_url         TEXT DEFAULT NULL,
            error_message  TEXT DEFAULT NULL,
            FOREIGN KEY (group_id) REFERENCES groups(id),
            FOREIGN KEY (bot_id) REFERENCES members(id)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_group_status "
        "ON agent_tasks(group_id, status)"
    )
    await db.commit()


async def migration_029(db):
    """Add durable idempotency reservations for agent task creation."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_task_requests (
            idempotency_key TEXT PRIMARY KEY,
            request_hash    TEXT NOT NULL,
            task_id         TEXT NOT NULL UNIQUE,
            state           TEXT NOT NULL DEFAULT 'pending'
                            CHECK(state IN ('pending', 'completed', 'failed')),
            error_message   TEXT DEFAULT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.commit()


async def migration_030(db):
    """Add tokenized, recoverable leases for coding-agent task retries."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_task_retry_claims (
            task_id         TEXT PRIMARY KEY,
            token           TEXT NOT NULL UNIQUE,
            previous_status TEXT NOT NULL,
            automatic      INTEGER NOT NULL DEFAULT 0,
            claimed_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
        )
    """)
    await db.commit()


async def migration_031(db):
    """Store control-plane authorization on users and bootstrap one operator."""
    await _safe_add_column(
        db,
        "ALTER TABLE users ADD COLUMN is_operator INTEGER NOT NULL DEFAULT 0 "
        "CHECK(is_operator IN (0, 1))",
    )
    cur = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    )
    if await cur.fetchone() is not None:
        cur = await db.execute("SELECT 1 FROM users WHERE is_operator = 1 LIMIT 1")
        if await cur.fetchone() is None:
            await db.execute(
                "UPDATE users SET is_operator = 1 WHERE id = COALESCE("
                "(SELECT id FROM users WHERE username = 'Nuke' LIMIT 1), "
                "(SELECT MIN(id) FROM users))"
            )
    await db.commit()


async def migration_032(db):
    """Add durable execution identity to group-domain tool traces.

    ``run_id`` is stable across a resumed agent session, ``step_id`` identifies
    one model iteration, and ``attempt_id`` identifies the concrete tool call.
    The columns remain empty for legacy/minimal callers that have no run scope.
    """
    cur = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_events'"
    )
    if await cur.fetchone() is None:
        return
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id          TEXT PRIMARY KEY,
            group_id        INTEGER NOT NULL,
            bot_id          INTEGER,
            thread_id       TEXT NOT NULL DEFAULT '',
            session_id      TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'running'
                            CHECK(status IN ('running', 'completed', 'failed', 'cancelled')),
            provider        TEXT NOT NULL DEFAULT '',
            model           TEXT NOT NULL DEFAULT '',
            executor        TEXT NOT NULL DEFAULT '',
            started_at      INTEGER NOT NULL,
            completed_at    INTEGER,
            iterations      INTEGER NOT NULL DEFAULT 0,
            input_tokens    INTEGER NOT NULL DEFAULT 0,
            output_tokens   INTEGER NOT NULL DEFAULT 0,
            error_summary   TEXT NOT NULL DEFAULT '',
            updated_at      INTEGER NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_group_started "
        "ON agent_runs(group_id, started_at DESC)"
    )
    await _safe_add_column(db, "ALTER TABLE tool_events ADD COLUMN run_id TEXT NOT NULL DEFAULT ''")
    await _safe_add_column(db, "ALTER TABLE tool_events ADD COLUMN step_id TEXT NOT NULL DEFAULT ''")
    await _safe_add_column(db, "ALTER TABLE tool_events ADD COLUMN attempt_id TEXT NOT NULL DEFAULT ''")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_events_run_step "
        "ON tool_events(group_id, run_id, step_id)"
    )
    await db.commit()


async def migration_033(db):
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_runs'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS agent_cases (
        case_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, group_id INTEGER NOT NULL,
        bot_id INTEGER, task TEXT NOT NULL DEFAULT '', task_signature TEXT NOT NULL DEFAULT '',
        tools_used TEXT NOT NULL DEFAULT '[]', files_touched TEXT NOT NULL DEFAULT '[]',
        attempts INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]',
        outcome TEXT NOT NULL, outcome_confidence REAL NOT NULL DEFAULT 0.0,
        verification_signals TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_cases_group_created ON agent_cases(group_id, created_at DESC)")
    await db.commit()


async def migration_034(db):
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_cases'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_records (
        record_id TEXT PRIMARY KEY, kind TEXT NOT NULL, group_id INTEGER NOT NULL,
        bot_id INTEGER, status TEXT NOT NULL DEFAULT 'active', content TEXT NOT NULL,
        task_signature TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0.0,
        importance REAL NOT NULL DEFAULT 0.0, source_ids TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}', algorithm_version TEXT NOT NULL DEFAULT 'experience-v1',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memory_records_lookup ON memory_records(group_id, bot_id, kind, status)")
    await db.execute("""CREATE TABLE IF NOT EXISTS experience_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL, run_id TEXT NOT NULL,
        group_id INTEGER NOT NULL, bot_id INTEGER, state TEXT NOT NULL,
        outcome TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0, tool_attempts INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(record_id, run_id))""")
    await db.commit()


async def migration_035(db):
    await _safe_add_column(db, "ALTER TABLE experience_usage ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0")
    await _safe_add_column(db, "ALTER TABLE experience_usage ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0")
    await _safe_add_column(db, "ALTER TABLE experience_usage ADD COLUMN tool_attempts INTEGER NOT NULL DEFAULT 0")
    await db.commit()


async def migration_036(db):
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_cases'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS pipeline_jobs (
        job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, group_id INTEGER NOT NULL,
        input_id TEXT NOT NULL, input_version TEXT NOT NULL DEFAULT '1',
        status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3, idempotency_key TEXT NOT NULL UNIQUE,
        lease_until INTEGER, error TEXT NOT NULL DEFAULT '', output_json TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER)""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_ready ON pipeline_jobs(group_id,status,updated_at)")
    await db.commit()


async def migration_037(db):
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_runs'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS run_decisions (
        decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, group_id INTEGER NOT NULL,
        bot_id INTEGER, step_id TEXT NOT NULL, decision_type TEXT NOT NULL,
        failure_class TEXT NOT NULL DEFAULT '', observation TEXT NOT NULL DEFAULT '',
        corrective_plan TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
        UNIQUE(run_id,step_id,decision_type))""")
    await db.commit()


async def migration_038(db):
    await _safe_add_column(db, "ALTER TABLE memory_records ADD COLUMN supporting_count INTEGER NOT NULL DEFAULT 1")
    await _safe_add_column(db, "ALTER TABLE memory_records ADD COLUMN contradicting_count INTEGER NOT NULL DEFAULT 0")
    await _safe_add_column(db, "ALTER TABLE memory_records ADD COLUMN last_used_at INTEGER")
    await _safe_add_column(db, "ALTER TABLE memory_records ADD COLUMN valid_to INTEGER")
    await _safe_add_column(db, "ALTER TABLE memory_records ADD COLUMN superseded_by TEXT")
    await db.commit()


async def migration_039(db):
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_records'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS skills (
        skill_id TEXT PRIMARY KEY,group_id INTEGER NOT NULL,bot_id INTEGER,name TEXT NOT NULL,
        maturity TEXT NOT NULL DEFAULT 'candidate',risk_level TEXT NOT NULL,current_version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'active',success_count INTEGER NOT NULL DEFAULT 0,failure_count INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(group_id,bot_id,name))""")
    await db.execute("""CREATE TABLE IF NOT EXISTS skill_versions (
        skill_id TEXT NOT NULL,version INTEGER NOT NULL,schema_version TEXT NOT NULL DEFAULT '1',
        declaration_json TEXT NOT NULL,content_hash TEXT NOT NULL,evidence_ids TEXT NOT NULL DEFAULT '[]',
        created_at INTEGER NOT NULL,PRIMARY KEY(skill_id,version))""")
    await db.execute("""CREATE TABLE IF NOT EXISTS skill_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,skill_id TEXT NOT NULL,version INTEGER NOT NULL,
        run_id TEXT NOT NULL,group_id INTEGER NOT NULL,outcome TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,
        UNIQUE(skill_id,run_id))""")
    await db.commit()


async def migration_040(db):
    await _safe_add_column(db, "ALTER TABLE pipeline_jobs ADD COLUMN lease_token TEXT DEFAULT NULL")
    await db.commit()


async def migration_041(db):
    await _safe_add_column(db, "ALTER TABLE skill_usage ADD COLUMN state TEXT NOT NULL DEFAULT 'injected'")
    await db.execute("UPDATE skill_usage SET state='executed' WHERE outcome IS NOT NULL AND outcome!=''")
    await db.commit()


async def migration_042(db):
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='skills'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS skill_promotion_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL,
        group_id INTEGER NOT NULL, actor_id TEXT NOT NULL, reason TEXT NOT NULL,
        from_maturity TEXT NOT NULL, to_maturity TEXT NOT NULL,
        created_at INTEGER NOT NULL)""")
    await db.execute("""CREATE TRIGGER IF NOT EXISTS skill_promotion_audit_no_update
        BEFORE UPDATE ON skill_promotion_audit BEGIN
        SELECT RAISE(ABORT, 'skill promotion audit is immutable'); END""")
    await db.execute("""CREATE TRIGGER IF NOT EXISTS skill_promotion_audit_no_delete
        BEFORE DELETE ON skill_promotion_audit BEGIN
        SELECT RAISE(ABORT, 'skill promotion audit is immutable'); END""")
    await db.commit()


async def migration_043(db):
    cur = await db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('users','groups')"
    )
    if (await cur.fetchone())[0] != 2:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS group_memberships (
        user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
        role TEXT NOT NULL DEFAULT 'member', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id,group_id),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE)""")
    # Preserve legacy single-user installations without granting any implicit
    # access when multiple identities already exist.
    await db.execute("""INSERT INTO group_memberships(user_id,group_id,role)
        SELECT u.id,g.id,'owner' FROM users u CROSS JOIN groups g
        WHERE (SELECT COUNT(*) FROM users)=1
        ON CONFLICT(user_id,group_id) DO NOTHING""")
    await db.commit()


async def migration_044(db):
    cur = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_records'"
    )
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_projection_outbox (
        event_id TEXT PRIMARY KEY, projection_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL, aggregate_version TEXT NOT NULL,
        group_id INTEGER NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0,
        next_attempt_at INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
        lease_until INTEGER, last_error TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER)""")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_memory_projection_outbox_ready
        ON memory_projection_outbox(group_id,status,next_attempt_at,updated_at)""")
    await db.commit()


async def migration_045(db):
    """Add evidence-bearing lifecycle fields to Experience and Skill usage."""
    usage_columns = {
        "experience_usage": (
            "adopted_at INTEGER",
            "executed_at INTEGER",
            "verified_at INTEGER",
            "adopted_via TEXT NOT NULL DEFAULT ''",
            "adoption_evidence_json TEXT NOT NULL DEFAULT '{}'",
            "execution_evidence_json TEXT NOT NULL DEFAULT '{}'",
            "verification_status TEXT NOT NULL DEFAULT 'unverified'",
            "verification_evidence_json TEXT NOT NULL DEFAULT '{}'",
        ),
        "skill_usage": (
            "adopted_at INTEGER",
            "executed_at INTEGER",
            "verified_at INTEGER",
            "adopted_via TEXT NOT NULL DEFAULT ''",
            "adoption_evidence_json TEXT NOT NULL DEFAULT '{}'",
            "execution_evidence_json TEXT NOT NULL DEFAULT '{}'",
            "verification_status TEXT NOT NULL DEFAULT 'unverified'",
            "verification_evidence_json TEXT NOT NULL DEFAULT '{}'",
            "updated_at INTEGER NOT NULL DEFAULT 0",
        ),
    }
    for table, columns in usage_columns.items():
        for column in columns:
            await _safe_add_column(db, f"ALTER TABLE {table} ADD COLUMN {column}")
    await db.commit()


async def migration_046(db):
    """Persist deterministic Case verdicts and correction evidence."""
    for column in (
        "outcome_status TEXT NOT NULL DEFAULT 'unverified_completion'",
        "verification_adapter TEXT NOT NULL DEFAULT ''",
        "correction_evidence_json TEXT NOT NULL DEFAULT '{}'",
    ):
        await _safe_add_column(
            db, f"ALTER TABLE agent_cases ADD COLUMN {column}"
        )
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
    migration_021,
    migration_022,
    migration_023,
    migration_024,
    migration_025,
    migration_026,
    migration_027,
    migration_028,
    migration_029,
    migration_030,
    migration_031,
    migration_032,
    migration_033,
    migration_034,
    migration_035,
    migration_036,
    migration_037,
    migration_038,
    migration_039,
    migration_040,
    migration_041,
    migration_042,
    migration_043,
    migration_044,
    migration_045,
    migration_046,
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
