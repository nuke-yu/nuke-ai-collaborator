"""Migration 042: immutable Skill promotion audit."""


async def apply(db) -> None:
    cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='skills'")
    if await cur.fetchone() is None:
        return
    await db.execute("""CREATE TABLE IF NOT EXISTS skill_promotion_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL,
        group_id INTEGER NOT NULL, actor_id TEXT NOT NULL, reason TEXT NOT NULL,
        from_maturity TEXT NOT NULL, to_maturity TEXT NOT NULL, created_at INTEGER NOT NULL)""")
    await db.execute("""CREATE TRIGGER IF NOT EXISTS skill_promotion_audit_no_update
        BEFORE UPDATE ON skill_promotion_audit BEGIN
        SELECT RAISE(ABORT, 'skill promotion audit is immutable'); END""")
    await db.execute("""CREATE TRIGGER IF NOT EXISTS skill_promotion_audit_no_delete
        BEFORE DELETE ON skill_promotion_audit BEGIN
        SELECT RAISE(ABORT, 'skill promotion audit is immutable'); END""")
    await db.commit()
