"""scheduler/store.py — async CRUD for the cron_jobs table."""
import aiosqlite
import db as _db


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return dict(row)


async def list_jobs(bot_id: int | None = None, group_id: int | None = None,
                    enabled_only: bool = False) -> list[dict]:
    conditions, params = [], []
    if bot_id is not None:
        conditions.append("bot_id = ?")
        params.append(bot_id)
    if group_id is not None:
        conditions.append("group_id = ?")
        params.append(group_id)
    if enabled_only:
        conditions.append("enabled = 1")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            f"SELECT * FROM cron_jobs {where} ORDER BY id", params
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_job(job_id: int) -> dict | None:
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM cron_jobs WHERE id = ?", (job_id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def create_job(bot_id: int, group_id: int, cron_expr: str, message: str,
                     label: str = "", enabled: bool = True) -> dict:
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """INSERT INTO cron_jobs (bot_id, group_id, cron_expr, message, label, enabled)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bot_id, group_id, cron_expr, message, label, int(enabled)),
        )
        await conn.commit()
        async with conn.execute(
            "SELECT * FROM cron_jobs WHERE id = ?", (cur.lastrowid,)
        ) as sel:
            row = await sel.fetchone()
    return _row_to_dict(row)


async def update_job(job_id: int, **fields) -> dict:
    allowed = {"cron_expr", "message", "label", "enabled"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return await get_job(job_id)
    if "enabled" in updates:
        updates["enabled"] = int(bool(updates["enabled"]))
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            f"UPDATE cron_jobs SET {set_clause} WHERE id = ?",
            list(updates.values()) + [job_id],
        )
        await conn.commit()
        async with conn.execute(
            "SELECT * FROM cron_jobs WHERE id = ?", (job_id,)
        ) as sel:
            row = await sel.fetchone()
    return _row_to_dict(row)


async def toggle_job(job_id: int) -> dict:
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            "UPDATE cron_jobs SET enabled = 1 - enabled WHERE id = ?", (job_id,)
        )
        await conn.commit()
        async with conn.execute(
            "SELECT * FROM cron_jobs WHERE id = ?", (job_id,)
        ) as sel:
            row = await sel.fetchone()
    return _row_to_dict(row)


async def update_last_run(job_id: int) -> None:
    async with _db.connect() as conn:
        await conn.execute(
            "UPDATE cron_jobs SET last_run_at = datetime('now') WHERE id = ?",
            (job_id,),
        )
        await conn.commit()


async def delete_job(job_id: int) -> None:
    async with _db.connect() as conn:
        await conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
        await conn.commit()
