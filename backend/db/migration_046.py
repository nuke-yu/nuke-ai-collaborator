"""Migration 046: deterministic Case verdict and correction evidence."""


async def apply(db, safe_add_column) -> None:
    for column in (
        "outcome_status TEXT NOT NULL DEFAULT 'unverified_completion'",
        "verification_adapter TEXT NOT NULL DEFAULT ''",
        "correction_evidence_json TEXT NOT NULL DEFAULT '{}'",
    ):
        await safe_add_column(db, f"ALTER TABLE agent_cases ADD COLUMN {column}")
    await db.commit()
