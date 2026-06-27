"""bot_skills capability/assignment store (Plan A).

The single truth source for "which external skills does this bot have, and is
each enabled". DELIBERATELY separate from permission_rules: this answers
capability/visibility; permission_rules answers call-time HIL (allow/ask/deny).

Central-DB table (see db/schema_split.py). Reads via db.global_db(); writes via
db.write_connect(db.DB_PATH) — mirrors permissions/db.py.
"""
import db as _db

# Skill layers whose visibility is gated by bot_skills. Non-external layers
# (system/group/role/learned) are always visible and never filtered here.
EXTERNAL_LAYERS = {"external_global", "external_group"}


async def set_assignment(bot_id: int, skill_name: str, pool: str,
                         enabled: bool = True, assigned_by: int | None = None) -> None:
    """Insert or update one assignment (UNIQUE(bot_id, skill_name))."""
    async with _db.write_connect(_db.DB_PATH) as db:
        await db.execute(
            """INSERT INTO bot_skills (bot_id, skill_name, pool, enabled, assigned_by)
               VALUES (?,?,?,?,?)
               ON CONFLICT(bot_id, skill_name) DO UPDATE SET
                   pool=excluded.pool,
                   enabled=excluded.enabled,
                   assigned_by=excluded.assigned_by""",
            (bot_id, skill_name, pool, 1 if enabled else 0, assigned_by),
        )
        await db.commit()


async def remove_assignment(bot_id: int, skill_name: str) -> None:
    async with _db.write_connect(_db.DB_PATH) as db:
        await db.execute(
            "DELETE FROM bot_skills WHERE bot_id=? AND skill_name=?",
            (bot_id, skill_name),
        )
        await db.commit()


async def list_assignments(bot_id: int) -> list[dict]:
    async with _db.global_db() as db:
        async with db.execute(
            "SELECT skill_name, pool, enabled, assigned_by "
            "FROM bot_skills WHERE bot_id=? ORDER BY skill_name",
            (bot_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"skill_name": r[0], "pool": r[1], "enabled": bool(r[2]), "assigned_by": r[3]}
        for r in rows
    ]


async def enabled_skill_names(bot_id: int) -> set[str]:
    async with _db.global_db() as db:
        async with db.execute(
            "SELECT skill_name FROM bot_skills WHERE bot_id=? AND enabled=1",
            (bot_id,),
        ) as cur:
            return {r[0] for r in await cur.fetchall()}
