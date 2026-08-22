"""Migration 061: add lifecycle metadata to group artifacts."""


async def apply(db, safe_add_column) -> None:
    for statement in (
        "ALTER TABLE group_artifacts ADD COLUMN artifact_version INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE group_artifacts ADD COLUMN parent_artifact_id TEXT DEFAULT NULL",
        "ALTER TABLE group_artifacts ADD COLUMN derives_from TEXT DEFAULT NULL",
        "ALTER TABLE group_artifacts ADD COLUMN created_by TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE group_artifacts ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL",
        "ALTER TABLE group_artifacts ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'",
    ):
        await safe_add_column(db, statement)
    await db.commit()
