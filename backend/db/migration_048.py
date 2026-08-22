"""Migration 048: structured task identity and Experience signatures."""


async def apply(db, safe_add_column) -> None:
    columns = {
        "agent_cases": (
            "semantic_cluster_key TEXT NOT NULL DEFAULT ''",
            "task_family TEXT NOT NULL DEFAULT 'other'",
            "task_concepts_json TEXT NOT NULL DEFAULT '[]'",
        ),
        "memory_records": (
            "semantic_cluster_key TEXT NOT NULL DEFAULT ''",
            "environment_signature TEXT NOT NULL DEFAULT ''",
            "failure_signature TEXT NOT NULL DEFAULT ''",
        ),
    }
    for table, declarations in columns.items():
        for declaration in declarations:
            await safe_add_column(db, f"ALTER TABLE {table} ADD COLUMN {declaration}")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_memory_records_semantic
        ON memory_records(group_id,bot_id,kind,status,semantic_cluster_key,
        environment_signature,failure_signature)""")
    await db.commit()
