"""Migration 047: ordered deterministic Case attempt trace."""


async def apply(db) -> None:
    await db.execute("""CREATE TABLE IF NOT EXISTS agent_case_attempts (
        case_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        group_id INTEGER NOT NULL, bot_id INTEGER,
        step_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
        phase TEXT NOT NULL, action_tool TEXT NOT NULL,
        action_target TEXT NOT NULL DEFAULT '', observation_status TEXT NOT NULL,
        observation_summary TEXT NOT NULL DEFAULT '', verifier_adapter TEXT NOT NULL DEFAULT '',
        verifies_task INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
        PRIMARY KEY(case_id, ordinal)
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_case_attempts_group_case ON agent_case_attempts(group_id,case_id,ordinal)")
    await db.commit()
