"""CELL-05: split schema into a central DB + per-group private DBs.

Project-Cell Isolation V3 §3 data-domain ownership. New databases (central.db and
each group_{id}/chat.db) are created directly at the FINAL schema (all columns
that the legacy linear migrations 001..N had added are inlined) and stamped to the
current version — so we never replay the mixed-domain migration history on a
fresh per-domain DB. Future schema changes are domain-tagged migrations (CELL-07).

CROSS-DOMAIN FK RULE (see the user-facing explanation): a FK can only be enforced
within one SQLite file. Group tables that referenced central tables
(messages.member_id/group_id, agent_sessions.bot_id/group_id, workflow_state /
group_locks → groups, group_locks.bot_id) DROP those FKs and keep the columns as
plain integers (logical references, app-enforced). FKs that stay WITHIN a domain
are preserved (message_reactions/pinned/embeddings → messages; session_events →
agent_sessions; members → groups; permission_rules/cron_jobs → members/groups —
all central-internal).

The legacy single-DB db.schema.init_db() is unchanged; these split inits are
additive, used by the future Supervisor (central) and Worker (per group).
"""
from db import DB_PATH
from db.migrations import MIGRATIONS
from db.schema import _seed_templates

# ── table → domain ────────────────────────────────────────────────────────
CENTRAL_TABLES = frozenset({
    "users", "groups", "members", "role_templates", "permission_rules", "cron_jobs",
    "unread_counts",
})
GROUP_TABLES = frozenset({
    "messages", "role_summaries", "message_embeddings", "member_read",
    "message_reactions", "pinned_messages", "agent_sessions", "session_events",
    "workflow_state", "group_locks", "tickets",
})

# ── CENTRAL DDL (final shape; central-internal FKs kept) ──────────────────
_CENTRAL_DDL = [
    """CREATE TABLE IF NOT EXISTS users (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        username     TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        email        TEXT,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",

    """CREATE TABLE IF NOT EXISTS groups (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        announcement TEXT DEFAULT NULL,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        assigned_worker_id TEXT DEFAULT 'w0'
    )""",
    """CREATE TABLE IF NOT EXISTS members (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id         INTEGER NOT NULL,
        user_id          INTEGER DEFAULT NULL,
        name             TEXT NOT NULL,
        type             TEXT NOT NULL CHECK(type IN ('human', 'bot')),
        role             TEXT,
        system_prompt    TEXT,
        avatar_color     TEXT    DEFAULT '#6366f1',
        model_provider   TEXT    DEFAULT 'deepseek',
        model_name       TEXT    DEFAULT 'deepseek-chat',
        auto_reply       TEXT    DEFAULT NULL,
        context_cleared_at TEXT  DEFAULT NULL,
        temperature      REAL    DEFAULT 0.7,
        max_tokens       INTEGER DEFAULT 4096,
        personality_prompt TEXT  DEFAULT NULL,
        executor_id      TEXT    DEFAULT 'tool_loop_v1',
        executor_config  TEXT    DEFAULT '{}',
        done_keyword     TEXT    DEFAULT NULL,
        traits_json      TEXT    DEFAULT '[]',
        FOREIGN KEY (group_id) REFERENCES groups(id)
    )""",
    """CREATE TABLE IF NOT EXISTS role_templates (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        role         TEXT NOT NULL,
        system_prompt TEXT NOT NULL,
        avatar_color TEXT DEFAULT '#6366f1'
    )""",
    """CREATE TABLE IF NOT EXISTS permission_rules (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id       INTEGER NOT NULL,
        tool_pattern TEXT    NOT NULL,
        args_pattern TEXT    NOT NULL DEFAULT '',
        action       TEXT    NOT NULL DEFAULT 'allow',
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (bot_id) REFERENCES members(id)
    )""",
    """CREATE TABLE IF NOT EXISTS cron_jobs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id     INTEGER NOT NULL,
        group_id   INTEGER NOT NULL,
        cron_expr  TEXT    NOT NULL,
        message    TEXT    NOT NULL,
        label      TEXT    DEFAULT '',
        enabled    INTEGER DEFAULT 1,
        created_at TEXT    DEFAULT (datetime('now')),
        last_run_at TIMESTAMP,
        FOREIGN KEY (bot_id)   REFERENCES members(id),
        FOREIGN KEY (group_id) REFERENCES groups(id)
    )""",
    # unread_counts: NEW central projection. Worker pushes deltas upstream; only
    # the Supervisor writes here (V3 §10.1).
    """CREATE TABLE IF NOT EXISTS unread_counts (
        member_id  INTEGER NOT NULL,
        group_id   INTEGER NOT NULL,
        unread     INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (member_id, group_id)
    )""",
]

# ── GROUP DDL (final shape; cross-domain FKs dropped, within-group FKs kept) ─
_GROUP_DDL = [
    # messages: DROP FK group_id->groups, member_id->members (cross-domain).
    """CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id    INTEGER NOT NULL,
        member_id   INTEGER NOT NULL,
        content     TEXT    NOT NULL,
        reply_to_id INTEGER DEFAULT NULL,
        edited_at   TIMESTAMP DEFAULT NULL,
        is_deleted  INTEGER DEFAULT 0,
        file_url    TEXT    DEFAULT NULL,
        file_name   TEXT    DEFAULT NULL,
        file_size   INTEGER DEFAULT NULL,
        file_type   TEXT    DEFAULT NULL,
        is_auto_reply INTEGER DEFAULT 0,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        input_tokens INTEGER DEFAULT NULL,
        output_tokens INTEGER DEFAULT NULL,
        cache_read_tokens INTEGER DEFAULT NULL,
        cache_creation_tokens INTEGER DEFAULT NULL,
        sender_name TEXT DEFAULT NULL,
        sender_type TEXT DEFAULT NULL,
        sender_avatar TEXT DEFAULT NULL,
        sender_provider TEXT DEFAULT NULL,
        sender_model TEXT DEFAULT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS role_summaries (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id           INTEGER NOT NULL,
        role               TEXT    NOT NULL,
        summary            TEXT    NOT NULL,
        covered_through_id INTEGER NOT NULL,
        bot_id             INTEGER DEFAULT NULL,
        created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    # within-group FK to messages: kept
    """CREATE TABLE IF NOT EXISTS message_embeddings (
        message_id INTEGER PRIMARY KEY,
        embedding  TEXT NOT NULL,
        FOREIGN KEY (message_id) REFERENCES messages(id)
    )""",
    """CREATE TABLE IF NOT EXISTS member_read (
        member_id    INTEGER NOT NULL,
        group_id     INTEGER NOT NULL,
        last_read_id INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (member_id, group_id)
    )""",
    """CREATE TABLE IF NOT EXISTS message_reactions (
        message_id INTEGER NOT NULL,
        member_id  INTEGER NOT NULL,
        emoji      TEXT    NOT NULL,
        PRIMARY KEY (message_id, member_id, emoji),
        FOREIGN KEY (message_id) REFERENCES messages(id)
    )""",
    """CREATE TABLE IF NOT EXISTS pinned_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id   INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        pinned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(group_id, message_id),
        FOREIGN KEY (message_id) REFERENCES messages(id)
    )""",
    # agent_sessions: DROP FK bot_id->members, group_id->groups (cross-domain).
    """CREATE TABLE IF NOT EXISTS agent_sessions (
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
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
        last_snapshot_json TEXT
    )""",
    # within-group FK to agent_sessions: kept
    """CREATE TABLE IF NOT EXISTS session_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        event_type  TEXT NOT NULL,
        payload     TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_session_events ON session_events(session_id, id)",
    # workflow_state: DROP FK group_id->groups (cross-domain).
    """CREATE TABLE IF NOT EXISTS workflow_state (
        group_id        INTEGER PRIMARY KEY,
        orchestrator_id TEXT NOT NULL DEFAULT 'workflow_v1',
        state_json      TEXT NOT NULL DEFAULT '{}',
        status          TEXT NOT NULL DEFAULT 'active',
        updated_at      TEXT DEFAULT (datetime('now'))
    )""",
    # group_locks: DROP FK group_id->groups, bot_id->members (cross-domain).
    """CREATE TABLE IF NOT EXISTS group_locks (
        group_id  INTEGER PRIMARY KEY,
        bot_id    INTEGER NOT NULL,
        locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT NOT NULL,
        group_id INTEGER NOT NULL,
        title TEXT,
        status TEXT DEFAULT 'backlog',
        assignee_id INTEGER,
        priority TEXT,
        metadata_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        total_usd_cost REAL DEFAULT 0.0,
        UNIQUE(group_id, ticket_id)
    )""",
]


async def _stamp_version(conn) -> None:
    """Mark a fresh per-domain DB at the current schema level so the legacy linear
    migrations never re-run on it; future domain-tagged migrations apply on top."""
    await conn.execute("""CREATE TABLE IF NOT EXISTS _schema_version (
        version INTEGER NOT NULL, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    cur = await conn.execute("SELECT MAX(version) FROM _schema_version")
    if (await cur.fetchone())[0] is None:
        await conn.execute("INSERT INTO _schema_version (version) VALUES (?)", (len(MIGRATIONS),))


async def init_central_db(path: str | None = None) -> None:
    """Create the central (Supervisor-owned) DB at the final schema + seed templates."""
    import db as _db
    async with _db.connect(path or DB_PATH) as conn:
        for ddl in _CENTRAL_DDL:
            await conn.execute(ddl)
        await conn.commit()
        await _stamp_version(conn)
        await _seed_templates(conn)
        await conn.commit()


async def init_group_db(path: str | None = None) -> None:
    """Create a per-group private DB at the final schema (cross-domain FKs dropped)."""
    import db as _db
    async with _db.connect(path or DB_PATH) as conn:
        for ddl in _GROUP_DDL:
            await conn.execute(ddl)
        await conn.commit()
        await _stamp_version(conn)
        await conn.commit()
