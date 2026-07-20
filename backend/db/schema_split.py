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
import logging

from db import DB_PATH
from db.migrations import MIGRATIONS
from db.schema import _seed_templates

log = logging.getLogger(__name__)

# ── table → domain ────────────────────────────────────────────────────────
CENTRAL_TABLES = frozenset({
    "users", "groups", "members", "role_templates", "permission_rules", "cron_jobs",
    "unread_counts", "bot_skills", "external_skills", "agent_tasks",
    "agent_task_requests", "agent_task_retry_claims",
})
GROUP_TABLES = frozenset({
    "messages", "role_summaries", "message_embeddings", "member_read",
    "message_reactions", "pinned_messages", "agent_sessions", "session_events",
    "workflow_state", "group_locks", "tickets", "reflection_state", "tool_events",
    "agent_runs", "agent_cases", "memory_records", "experience_usage", "pipeline_jobs", "run_decisions",
    "skills", "skill_versions", "skill_usage",
})

# ── CENTRAL DDL (final shape; central-internal FKs kept) ──────────────────
_CENTRAL_DDL = [
    """CREATE TABLE IF NOT EXISTS users (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        username     TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        email        TEXT,
        is_operator  INTEGER NOT NULL DEFAULT 0 CHECK(is_operator IN (0, 1)),
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",

    """CREATE TABLE IF NOT EXISTS groups (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        announcement TEXT DEFAULT NULL,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- NULL = unassigned → routed by deterministic modulo spread (group_id %
        -- num_workers). A non-NULL value is an explicit pin (reassign_group /
        -- create-time assignment). Default must NOT be 'w0' or every new group
        -- would pile onto worker w0 (the hotspot bug).
        assigned_worker_id TEXT DEFAULT NULL,
        away_summary TEXT DEFAULT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS members (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id         INTEGER NOT NULL,
        -- RESERVED for future multi-tenant isolation; NOT populated today.
        -- This is a trusted internal/single-machine tool, so WS auth is token-only
        -- (see main.py). Do NOT add a `members.user_id == token uid` check while this
        -- stays NULL — it rejects every connection and bricks the WebSocket (DFT-082).
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
        max_tokens       INTEGER DEFAULT 8192,
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
    # bot_skills: per-bot capability/assignment truth source (Plan A). enabled
    # toggles visibility WITHOUT removing the assignment; assigned_by/at audit it.
    # Separate from permission_rules (which only gates HIL at call time).
    """CREATE TABLE IF NOT EXISTS bot_skills (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id      INTEGER NOT NULL,
        skill_name  TEXT    NOT NULL,
        pool        TEXT    NOT NULL DEFAULT 'external_global',
        enabled     INTEGER NOT NULL DEFAULT 1,
        assigned_by INTEGER,
        assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bot_id, skill_name),
        FOREIGN KEY (bot_id) REFERENCES members(id)
    )""",
    # external_skills: import registry — provenance + lifecycle truth source.
    # group_id uses 0 (NOT NULL) for global scope so UNIQUE actually fires
    # (SQLite treats multiple NULLs as distinct).
    """CREATE TABLE IF NOT EXISTS external_skills (
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
    )""",
    """CREATE TABLE IF NOT EXISTS agent_tasks (
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
    )""",
    """CREATE TABLE IF NOT EXISTS agent_task_requests (
        idempotency_key TEXT PRIMARY KEY,
        request_hash    TEXT NOT NULL,
        task_id         TEXT NOT NULL UNIQUE,
        state           TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending', 'completed', 'failed')),
        error_message   TEXT DEFAULT NULL,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS agent_task_retry_claims (
        task_id         TEXT PRIMARY KEY,
        token           TEXT NOT NULL UNIQUE,
        previous_status TEXT NOT NULL,
        automatic      INTEGER NOT NULL DEFAULT 0,
        claimed_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
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
        sender_model TEXT DEFAULT NULL,
        meta TEXT DEFAULT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS role_summaries (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id           INTEGER NOT NULL,
        role               TEXT    NOT NULL,
        summary            TEXT    NOT NULL,
        covered_through_id INTEGER NOT NULL,
        bot_id             INTEGER DEFAULT NULL,
        thread_id          TEXT    DEFAULT NULL,
        created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    # Reflection watermark per (bot, group, thread): timestamp of the newest fact already
    # consolidated, so consolidation only reflects over NEW facts (P1 巩固层).
    """CREATE TABLE IF NOT EXISTS reflection_state (
        bot_id             INTEGER NOT NULL,
        group_id           INTEGER NOT NULL,
        thread_id          TEXT    NOT NULL DEFAULT '',
        covered_through_ts REAL    NOT NULL DEFAULT 0,
        updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (bot_id, group_id, thread_id)
    )""",
    # within-group FK to messages: kept
    """CREATE TABLE IF NOT EXISTS message_embeddings (
        message_id INTEGER PRIMARY KEY,
        embedding  TEXT NOT NULL,
        FOREIGN KEY (message_id) REFERENCES messages(id)
    )""",
    """CREATE TABLE IF NOT EXISTS member_read (
        member_id         INTEGER NOT NULL,
        group_id          INTEGER NOT NULL,
        last_read_id      INTEGER NOT NULL DEFAULT 0,
        last_recap_ack_id INTEGER NOT NULL DEFAULT 0,
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
        project TEXT DEFAULT '',
        assignee_id INTEGER,
        priority TEXT,
        metadata_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        total_usd_cost REAL DEFAULT 0.0,
        UNIQUE(group_id, ticket_id)
    )""",
    # tool_events (L1): deterministic per-tool-call event log (zero LLM). Every
    # tool dispatched through executors/tool_dispatch.dispatch_tool — builtin /
    # skill / shell AND MCP — appends one structured row here, fire-and-forget.
    # This is the recall floor for "what happened" without paying a model to
    # narrate it. Cross-domain FKs dropped (group_id/bot_id are bare ints).
    """CREATE TABLE IF NOT EXISTS tool_events (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ts             INTEGER NOT NULL,          -- epoch ms
        group_id       INTEGER NOT NULL,
        bot_id         INTEGER,
        thread_id      TEXT    NOT NULL DEFAULT '',
        tool           TEXT    NOT NULL,
        args_summary   TEXT    NOT NULL DEFAULT '',
        result_summary TEXT    NOT NULL DEFAULT '',
        is_error       INTEGER NOT NULL DEFAULT 0,
        files_touched  TEXT    NOT NULL DEFAULT '[]',  -- JSON array of paths
        command        TEXT,                           -- run_shell cmd, else NULL
        run_id         TEXT    NOT NULL DEFAULT '',
        step_id        TEXT    NOT NULL DEFAULT '',
        attempt_id     TEXT    NOT NULL DEFAULT '',
        -- L4: 0 = not yet folded into a durable summary; 1 = compressed (then
        -- prunable). maybe_compress_tool_events advances this in batches.
        compressed     INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_tool_events_grp_ts ON tool_events(group_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_tool_events_uncompressed ON tool_events(group_id, bot_id, compressed)",
    "CREATE INDEX IF NOT EXISTS idx_tool_events_run_step ON tool_events(group_id, run_id, step_id)",
    """CREATE TABLE IF NOT EXISTS agent_runs (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_runs_group_started ON agent_runs(group_id, started_at DESC)",
    """CREATE TABLE IF NOT EXISTS agent_cases (
        case_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, group_id INTEGER NOT NULL,
        bot_id INTEGER, task TEXT NOT NULL DEFAULT '', task_signature TEXT NOT NULL DEFAULT '',
        tools_used TEXT NOT NULL DEFAULT '[]', files_touched TEXT NOT NULL DEFAULT '[]',
        attempts INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]',
        outcome TEXT NOT NULL, outcome_confidence REAL NOT NULL DEFAULT 0.0,
        verification_signals TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_cases_group_created ON agent_cases(group_id, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS memory_records (
        record_id TEXT PRIMARY KEY, kind TEXT NOT NULL, group_id INTEGER NOT NULL,
        bot_id INTEGER, status TEXT NOT NULL DEFAULT 'active', content TEXT NOT NULL,
        task_signature TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0.0,
        importance REAL NOT NULL DEFAULT 0.0, source_ids TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}', algorithm_version TEXT NOT NULL DEFAULT 'experience-v1',
        supporting_count INTEGER NOT NULL DEFAULT 1, contradicting_count INTEGER NOT NULL DEFAULT 0,
        last_used_at INTEGER, valid_to INTEGER, superseded_by TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memory_records_lookup ON memory_records(group_id, bot_id, kind, status)",
    """CREATE TABLE IF NOT EXISTS experience_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL, run_id TEXT NOT NULL,
        group_id INTEGER NOT NULL, bot_id INTEGER, state TEXT NOT NULL,
        outcome TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0, tool_attempts INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(record_id, run_id)
    )""",
    """CREATE TABLE IF NOT EXISTS pipeline_jobs (
        job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, group_id INTEGER NOT NULL,
        input_id TEXT NOT NULL, input_version TEXT NOT NULL DEFAULT '1',
        status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3, idempotency_key TEXT NOT NULL UNIQUE,
        lease_until INTEGER, error TEXT NOT NULL DEFAULT '', output_json TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_ready ON pipeline_jobs(group_id, status, updated_at)",
    """CREATE TABLE IF NOT EXISTS run_decisions (
        decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, group_id INTEGER NOT NULL,
        bot_id INTEGER, step_id TEXT NOT NULL, decision_type TEXT NOT NULL,
        failure_class TEXT NOT NULL DEFAULT '', observation TEXT NOT NULL DEFAULT '',
        corrective_plan TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
        UNIQUE(run_id, step_id, decision_type)
    )""",
    """CREATE TABLE IF NOT EXISTS skills (
        skill_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL, bot_id INTEGER,
        name TEXT NOT NULL, maturity TEXT NOT NULL DEFAULT 'candidate', risk_level TEXT NOT NULL,
        current_version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active',
        success_count INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(group_id,bot_id,name)
    )""",
    """CREATE TABLE IF NOT EXISTS skill_versions (
        skill_id TEXT NOT NULL, version INTEGER NOT NULL, schema_version TEXT NOT NULL DEFAULT '1',
        declaration_json TEXT NOT NULL, content_hash TEXT NOT NULL, evidence_ids TEXT NOT NULL DEFAULT '[]',
        created_at INTEGER NOT NULL, PRIMARY KEY(skill_id,version)
    )""",
    """CREATE TABLE IF NOT EXISTS skill_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL, version INTEGER NOT NULL,
        run_id TEXT NOT NULL, group_id INTEGER NOT NULL, outcome TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, UNIQUE(skill_id,run_id)
    )""",
]

# FTS5 ranked search over tool_events (L3 upgrade). Kept separate and applied
# best-effort: a SQLite build without FTS5 must NOT brick group-DB init —
# search_events falls back to LIKE when tool_events_fts is absent. Mirrors
# migration_027 (which brings legacy group DBs up to the same shape).
_GROUP_FTS_DDL = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS tool_events_fts USING fts5("
    "tool, args_summary, result_summary, command, files_touched, "
    "content='tool_events', content_rowid='id')",
    "CREATE TRIGGER IF NOT EXISTS tool_events_fts_ai AFTER INSERT ON tool_events BEGIN "
    "INSERT INTO tool_events_fts(rowid, tool, args_summary, result_summary, command, files_touched) "
    "VALUES (new.id, new.tool, new.args_summary, new.result_summary, new.command, new.files_touched); END",
    "CREATE TRIGGER IF NOT EXISTS tool_events_fts_ad AFTER DELETE ON tool_events BEGIN "
    "INSERT INTO tool_events_fts(tool_events_fts, rowid, tool, args_summary, result_summary, command, files_touched) "
    "VALUES ('delete', old.id, old.tool, old.args_summary, old.result_summary, old.command, old.files_touched); END",
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
    """Create the central (Supervisor-owned) DB at the final schema + seed templates.

    Also runs run_migrations so a *legacy* central DB (the pre-split single DB
    repurposed as central) catches up on migrations added after it was stamped —
    e.g. messages.meta (migration 016), without which HTTP read endpoints that
    fall back to the central DB raise `no such column: m.meta`. On a fresh central
    DB _stamp_version marks it at len(MIGRATIONS), so run_migrations is a no-op and
    won't touch group-only tables that the central schema intentionally omits."""
    from db.migrations import run_migrations
    import db as _db
    async with _db.connect(path or DB_PATH) as conn:
        for ddl in _CENTRAL_DDL:
            await conn.execute(ddl)
        await conn.commit()
        await _stamp_version(conn)
        await run_migrations(conn)
        await _seed_templates(conn)
        await conn.commit()


async def init_group_db(path: str | None = None) -> None:
    """Create a per-group private DB at the final schema (cross-domain FKs dropped)."""
    import db as _db
    async with _db.connect(path or DB_PATH) as conn:
        for ddl in _GROUP_DDL:
            try:
                await conn.execute(ddl)
            except Exception as e:
                if "CREATE INDEX" in ddl.upper():
                    log.warning("init_group_db: index creation deferred (column might be missing before migrations): %s", e)
                else:
                    raise
        await conn.commit()
        # FTS5 index — best-effort so a build without FTS5 still yields a usable
        # group DB (search_events degrades to LIKE).
        try:
            for ddl in _GROUP_FTS_DDL:
                await conn.execute(ddl)
            await conn.commit()
        except Exception as e:
            log.warning("init_group_db: FTS5 unavailable, tool_events_fts skipped (search falls back to LIKE): %s", e)
        await _stamp_version(conn)
        await conn.commit()


# Per-process cache of group DBs already brought to current schema this process lifetime.
# Lets ensure_group_db_ready be attached broadly (route-layer dep) without paying a full
# init+migrate on every request. Cleared on restart, so a deploy that adds migrations
# re-runs them. Idempotent ops mean a concurrent first-time double-run is harmless.
_ready_group_dbs: set[str] = set()


async def ensure_group_db_ready(path: str) -> None:
    """Ensure the group DB at path exists, schema is initialized, and migrations are run.
    No-ops after the first successful call for a given path this process (see _ready_group_dbs)."""
    if path in _ready_group_dbs:
        return
    import os
    import db as _db
    from db.migrations import run_migrations
    os.makedirs(os.path.dirname(path), exist_ok=True)
    await init_group_db(path)
    async with _db.connect(path) as conn:
        await run_migrations(conn)
    _ready_group_dbs.add(path)
