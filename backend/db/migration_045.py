"""Migration 045: evidence-bearing Experience and Skill usage lifecycle."""


async def apply(db, safe_add_column) -> None:
    usage_columns = {
        "experience_usage": (
            "adopted_at INTEGER", "executed_at INTEGER", "verified_at INTEGER",
            "adopted_via TEXT NOT NULL DEFAULT ''", "adoption_evidence_json TEXT NOT NULL DEFAULT '{}'",
            "execution_evidence_json TEXT NOT NULL DEFAULT '{}'", "verification_status TEXT NOT NULL DEFAULT 'unverified'",
            "verification_evidence_json TEXT NOT NULL DEFAULT '{}'",
        ),
        "skill_usage": (
            "adopted_at INTEGER", "executed_at INTEGER", "verified_at INTEGER",
            "adopted_via TEXT NOT NULL DEFAULT ''", "adoption_evidence_json TEXT NOT NULL DEFAULT '{}'",
            "execution_evidence_json TEXT NOT NULL DEFAULT '{}'", "verification_status TEXT NOT NULL DEFAULT 'unverified'",
            "verification_evidence_json TEXT NOT NULL DEFAULT '{}'", "updated_at INTEGER NOT NULL DEFAULT 0",
        ),
    }
    for table, columns in usage_columns.items():
        for column in columns:
            await safe_add_column(db, f"ALTER TABLE {table} ADD COLUMN {column}")
    await db.commit()
