"""Versioned schema manifest owned by the Memory bounded context."""
from __future__ import annotations

from typing import Final

from memory.contracts import MemoryOperationError
from memory.ports import MemoryDatabasePort

MEMORY_SCHEMA_VERSION: Final = 6

MEMORY_GROUP_TABLES = frozenset(
    {
        "memory_schema_version",
        "agent_cases",
        "agent_case_attempts",
        "memory_records",
        "memory_projection_outbox",
        "experience_usage",
        "pipeline_jobs",
        "skills",
        "skill_versions",
        "skill_usage",
        "skill_promotion_audit",
    }
)

_VERSION_TABLE_DDL = """CREATE TABLE IF NOT EXISTS memory_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

MEMORY_V1_DDL = (
    """CREATE TABLE IF NOT EXISTS agent_cases (
        case_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, group_id INTEGER NOT NULL,
        bot_id INTEGER, task TEXT NOT NULL DEFAULT '', task_signature TEXT NOT NULL DEFAULT '',
        semantic_cluster_key TEXT NOT NULL DEFAULT '',
        task_family TEXT NOT NULL DEFAULT 'other',
        task_concepts_json TEXT NOT NULL DEFAULT '[]',
        tools_used TEXT NOT NULL DEFAULT '[]', files_touched TEXT NOT NULL DEFAULT '[]',
        attempts INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]',
        outcome TEXT NOT NULL, outcome_confidence REAL NOT NULL DEFAULT 0.0,
        outcome_status TEXT NOT NULL DEFAULT 'unverified_completion',
        verification_adapter TEXT NOT NULL DEFAULT '',
        correction_evidence_json TEXT NOT NULL DEFAULT '{}',
        verification_signals TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_cases_group_created ON agent_cases(group_id, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS agent_case_attempts (
        case_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        group_id INTEGER NOT NULL, bot_id INTEGER,
        step_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
        phase TEXT NOT NULL, action_tool TEXT NOT NULL,
        action_target TEXT NOT NULL DEFAULT '',
        observation_status TEXT NOT NULL,
        observation_summary TEXT NOT NULL DEFAULT '',
        verifier_adapter TEXT NOT NULL DEFAULT '',
        verifies_task INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(case_id, ordinal)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_case_attempts_group_case ON agent_case_attempts(group_id,case_id,ordinal)",
    """CREATE TABLE IF NOT EXISTS memory_records (
        record_id TEXT PRIMARY KEY, kind TEXT NOT NULL, group_id INTEGER NOT NULL,
        bot_id INTEGER, status TEXT NOT NULL DEFAULT 'active', content TEXT NOT NULL,
        task_signature TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0.0,
        semantic_cluster_key TEXT NOT NULL DEFAULT '',
        environment_signature TEXT NOT NULL DEFAULT '',
        failure_signature TEXT NOT NULL DEFAULT '',
        owner_type TEXT NOT NULL DEFAULT 'bot',
        authority TEXT NOT NULL DEFAULT 'bot_observation',
        subject_key TEXT NOT NULL DEFAULT '',
        sensitivity TEXT NOT NULL DEFAULT 'group',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        created_by TEXT NOT NULL DEFAULT '',
        effective_from INTEGER,
        importance REAL NOT NULL DEFAULT 0.0, source_ids TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}', algorithm_version TEXT NOT NULL DEFAULT 'experience-v1',
        supporting_count INTEGER NOT NULL DEFAULT 1, contradicting_count INTEGER NOT NULL DEFAULT 0,
        last_used_at INTEGER, valid_to INTEGER, superseded_by TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memory_records_lookup ON memory_records(group_id, bot_id, kind, status)",
    "CREATE INDEX IF NOT EXISTS idx_memory_records_semantic ON memory_records(group_id,bot_id,kind,status,semantic_cluster_key,environment_signature,failure_signature)",
    "CREATE INDEX IF NOT EXISTS idx_memory_records_group_facts ON memory_records(group_id,owner_type,kind,status,subject_key,updated_at DESC)",
    """CREATE TABLE IF NOT EXISTS memory_projection_outbox (
        event_id TEXT PRIMARY KEY, projection_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL, aggregate_version TEXT NOT NULL,
        group_id INTEGER NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0,
        next_attempt_at INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
        lease_until INTEGER, last_error TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memory_projection_outbox_ready ON memory_projection_outbox(group_id,status,next_attempt_at,updated_at)",
    """CREATE TABLE IF NOT EXISTS experience_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL, run_id TEXT NOT NULL,
        group_id INTEGER NOT NULL, bot_id INTEGER, state TEXT NOT NULL,
        outcome TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0, tool_attempts INTEGER NOT NULL DEFAULT 0,
        adopted_at INTEGER, executed_at INTEGER, verified_at INTEGER,
        adopted_via TEXT NOT NULL DEFAULT '',
        adoption_evidence_json TEXT NOT NULL DEFAULT '{}',
        execution_evidence_json TEXT NOT NULL DEFAULT '{}',
        verification_status TEXT NOT NULL DEFAULT 'unverified',
        verification_evidence_json TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(record_id, run_id)
    )""",
    """CREATE TABLE IF NOT EXISTS pipeline_jobs (
        job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, group_id INTEGER NOT NULL,
        input_id TEXT NOT NULL, input_version TEXT NOT NULL DEFAULT '1',
        status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3, idempotency_key TEXT NOT NULL UNIQUE,
        lease_until INTEGER, lease_token TEXT DEFAULT NULL, error TEXT NOT NULL DEFAULT '',
        output_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, completed_at INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_ready ON pipeline_jobs(group_id, status, updated_at)",
    """CREATE TABLE IF NOT EXISTS skills (
        skill_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL, bot_id INTEGER,
        name TEXT NOT NULL, maturity TEXT NOT NULL DEFAULT 'candidate', risk_level TEXT NOT NULL,
        current_version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active',
        success_count INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(group_id,bot_id,name)
    )""",
    """CREATE TABLE IF NOT EXISTS skill_versions (
        skill_id TEXT NOT NULL, version INTEGER NOT NULL,
        schema_version TEXT NOT NULL DEFAULT '1', declaration_json TEXT NOT NULL,
        content_hash TEXT NOT NULL, evidence_ids TEXT NOT NULL DEFAULT '[]',
        created_at INTEGER NOT NULL, PRIMARY KEY(skill_id,version)
    )""",
    """CREATE TABLE IF NOT EXISTS skill_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL, version INTEGER NOT NULL,
        run_id TEXT NOT NULL, group_id INTEGER NOT NULL, outcome TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT 'injected',
        adopted_at INTEGER, executed_at INTEGER, verified_at INTEGER,
        adopted_via TEXT NOT NULL DEFAULT '',
        adoption_evidence_json TEXT NOT NULL DEFAULT '{}',
        execution_evidence_json TEXT NOT NULL DEFAULT '{}',
        verification_status TEXT NOT NULL DEFAULT 'unverified',
        verification_evidence_json TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(skill_id,run_id)
    )""",
    """CREATE TABLE IF NOT EXISTS skill_promotion_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL,
        group_id INTEGER NOT NULL, actor_id TEXT NOT NULL, reason TEXT NOT NULL,
        from_maturity TEXT NOT NULL, to_maturity TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""",
    """CREATE TRIGGER IF NOT EXISTS skill_promotion_audit_no_update
        BEFORE UPDATE ON skill_promotion_audit BEGIN
        SELECT RAISE(ABORT, 'skill promotion audit is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS skill_promotion_audit_no_delete
        BEFORE DELETE ON skill_promotion_audit BEGIN
        SELECT RAISE(ABORT, 'skill promotion audit is immutable'); END""",
)

MEMORY_V2_COLUMNS = {
    "experience_usage": (
        ("adopted_at", "INTEGER"),
        ("executed_at", "INTEGER"),
        ("verified_at", "INTEGER"),
        ("adopted_via", "TEXT NOT NULL DEFAULT ''"),
        ("adoption_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("execution_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("verification_status", "TEXT NOT NULL DEFAULT 'unverified'"),
        ("verification_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
    ),
    "skill_usage": (
        ("adopted_at", "INTEGER"),
        ("executed_at", "INTEGER"),
        ("verified_at", "INTEGER"),
        ("adopted_via", "TEXT NOT NULL DEFAULT ''"),
        ("adoption_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("execution_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("verification_status", "TEXT NOT NULL DEFAULT 'unverified'"),
        ("verification_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("updated_at", "INTEGER NOT NULL DEFAULT 0"),
    ),
}

MEMORY_V3_COLUMNS = {
    "agent_cases": (
        ("outcome_status", "TEXT NOT NULL DEFAULT 'unverified_completion'"),
        ("verification_adapter", "TEXT NOT NULL DEFAULT ''"),
        ("correction_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
    ),
}

MEMORY_V5_COLUMNS = {
    "agent_cases": (
        ("semantic_cluster_key", "TEXT NOT NULL DEFAULT ''"),
        ("task_family", "TEXT NOT NULL DEFAULT 'other'"),
        ("task_concepts_json", "TEXT NOT NULL DEFAULT '[]'"),
    ),
    "memory_records": (
        ("semantic_cluster_key", "TEXT NOT NULL DEFAULT ''"),
        ("environment_signature", "TEXT NOT NULL DEFAULT ''"),
        ("failure_signature", "TEXT NOT NULL DEFAULT ''"),
    ),
}

MEMORY_V6_COLUMNS = {
    "memory_records": (
        ("owner_type", "TEXT NOT NULL DEFAULT 'bot'"),
        ("authority", "TEXT NOT NULL DEFAULT 'bot_observation'"),
        ("subject_key", "TEXT NOT NULL DEFAULT ''"),
        ("sensitivity", "TEXT NOT NULL DEFAULT 'group'"),
        ("evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("created_by", "TEXT NOT NULL DEFAULT ''"),
        ("effective_from", "INTEGER"),
    ),
}

MEMORY_GROUP_DDL = (
    _VERSION_TABLE_DDL,
    *MEMORY_V1_DDL,
)


class MemorySchemaManager:
    """Apply Memory-owned schema versions through an injected database port."""

    def __init__(self, database: MemoryDatabasePort) -> None:
        self._database = database

    async def ensure_group(self, group_id: int) -> int:
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        # memory_records is present in both legacy single-DB installations and
        # split group DBs, so it is the stable routing anchor during migration.
        async with await self._database.connect(
            "memory_records", group_id, write=True
        ) as connection:
            await connection.execute(_VERSION_TABLE_DDL)
            async with connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM memory_schema_version"
            ) as cursor:
                current = int((await cursor.fetchone())[0])
            if current > MEMORY_SCHEMA_VERSION:
                raise MemoryOperationError(
                    "memory schema is newer than this runtime "
                    f"(database={current}, runtime={MEMORY_SCHEMA_VERSION})"
                )
            # Reapply idempotent final-shape DDL to repair missing derived
            # tables, indexes, or governance triggers after external damage.
            for statement in MEMORY_V1_DDL:
                await connection.execute(statement)
            if current < 2:
                for table, columns in MEMORY_V2_COLUMNS.items():
                    async with connection.execute(
                        f"PRAGMA table_info({table})"
                    ) as cursor:
                        existing = {str(row[1]) for row in await cursor.fetchall()}
                    for name, declaration in columns:
                        if name not in existing:
                            await connection.execute(
                                f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                            )
            if current < 3:
                for table, columns in MEMORY_V3_COLUMNS.items():
                    async with connection.execute(
                        f"PRAGMA table_info({table})"
                    ) as cursor:
                        existing = {str(row[1]) for row in await cursor.fetchall()}
                    for name, declaration in columns:
                        if name not in existing:
                            await connection.execute(
                                f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                            )
            if current < 1:
                await connection.execute(
                    "INSERT INTO memory_schema_version(version) VALUES (1)",
                )
            if current < 2:
                await connection.execute(
                    "INSERT INTO memory_schema_version(version) VALUES (2)"
                )
            if current < 3:
                await connection.execute(
                    "INSERT INTO memory_schema_version(version) VALUES (3)"
                )
            if current < 4:
                await connection.execute(
                    "INSERT INTO memory_schema_version(version) VALUES (4)"
                )
            if current < 5:
                for table, columns in MEMORY_V5_COLUMNS.items():
                    async with connection.execute(
                        f"PRAGMA table_info({table})"
                    ) as cursor:
                        existing = {str(row[1]) for row in await cursor.fetchall()}
                    for name, declaration in columns:
                        if name not in existing:
                            await connection.execute(
                                f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                            )
                await connection.execute(
                    """CREATE INDEX IF NOT EXISTS idx_memory_records_semantic
                    ON memory_records(group_id,bot_id,kind,status,
                    semantic_cluster_key,environment_signature,failure_signature)"""
                )
                await connection.execute(
                    "INSERT INTO memory_schema_version(version) VALUES (5)"
                )
            if current < 6:
                async with connection.execute(
                    "PRAGMA table_info(memory_records)"
                ) as cursor:
                    existing = {str(row[1]) for row in await cursor.fetchall()}
                for name, declaration in MEMORY_V6_COLUMNS["memory_records"]:
                    if name not in existing:
                        await connection.execute(
                            f"ALTER TABLE memory_records ADD COLUMN "
                            f"{name} {declaration}"
                        )
                await connection.execute(
                    """CREATE INDEX IF NOT EXISTS idx_memory_records_group_facts
                    ON memory_records(group_id,owner_type,kind,status,
                    subject_key,updated_at DESC)"""
                )
                await connection.execute(
                    "INSERT INTO memory_schema_version(version) VALUES (6)"
                )
            await connection.commit()
        return MEMORY_SCHEMA_VERSION
