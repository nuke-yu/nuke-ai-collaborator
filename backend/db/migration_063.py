"""Migration 063: scope Case run identity by group."""
from __future__ import annotations


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_cases'")
    if await cur.fetchone() is None:
        return
    await db.execute("ALTER TABLE agent_cases RENAME TO agent_cases_v062")
    await db.execute("""CREATE TABLE agent_cases (
        case_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, group_id INTEGER NOT NULL,
        bot_id INTEGER, task TEXT NOT NULL DEFAULT '', task_signature TEXT NOT NULL DEFAULT '',
        semantic_cluster_key TEXT NOT NULL DEFAULT '', task_family TEXT NOT NULL DEFAULT 'other',
        task_concepts_json TEXT NOT NULL DEFAULT '[]', tools_used TEXT NOT NULL DEFAULT '[]',
        files_touched TEXT NOT NULL DEFAULT '[]', attempts INTEGER NOT NULL DEFAULT 0,
        errors TEXT NOT NULL DEFAULT '[]', outcome TEXT NOT NULL,
        outcome_confidence REAL NOT NULL DEFAULT 0.0,
        outcome_status TEXT NOT NULL DEFAULT 'unverified_completion',
        verification_adapter TEXT NOT NULL DEFAULT '', correction_evidence_json TEXT NOT NULL DEFAULT '{}',
        verification_signals TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(group_id, run_id)
    )""")
    await db.execute("""INSERT INTO agent_cases
        SELECT case_id,run_id,group_id,bot_id,task,task_signature,
               semantic_cluster_key,task_family,task_concepts_json,tools_used,
               files_touched,attempts,errors,outcome,outcome_confidence,
               outcome_status,verification_adapter,correction_evidence_json,
               verification_signals,summary,created_at,updated_at
        FROM agent_cases_v062""")
    await db.execute("DROP TABLE agent_cases_v062")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_cases_group_created ON agent_cases(group_id, created_at DESC)")
    await db.commit()
