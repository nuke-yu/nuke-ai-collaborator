"""external_skills registry CRUD (Plan B) — provenance + lifecycle truth source.

The file content is the skill truth source (scanner reads disk); this registry
row records WHERE it came from (source/ref/commit), its declared version /
platforms, the high-privilege tools it touches, and who imported it. import
writes file + row together; remove deletes both.

Central-DB table. Reads via db.global_db(); writes via db.write_connect — mirrors
skills/assignment.py.
"""
import sqlite3
import db as _db

# Sentinel group_id for global-scope rows (matches external_skills DEFAULT 0).
GLOBAL_GROUP_ID = 0

_COLS = ["id", "name", "scope_kind", "group_id", "source_url", "ref",
         "commit_sha", "version", "platforms", "high_privilege",
         "imported_by", "imported_at", "status"]


def _row_to_dict(row) -> dict:
    return {c: row[i] for i, c in enumerate(_COLS)}


async def register(name: str, scope_kind: str, group_id: int, source_url: str,
                   ref: str, commit_sha: str, version: str, platforms: str,
                   high_privilege: str, imported_by: int | None) -> int:
    async with _db.write_connect(_db.DB_PATH) as db:
        try:
            cur = await db.execute(
                """INSERT INTO external_skills
                   (name, scope_kind, group_id, source_url, ref, commit_sha,
                    version, platforms, high_privilege, imported_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name, scope_kind, group_id, source_url, ref, commit_sha,
                 version, platforms, high_privilege, imported_by),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("duplicate") from e
        await db.commit()
        return cur.lastrowid


async def list_external(scope_kind: str | None = None,
                        group_id: int | None = None) -> list[dict]:
    sql = f"SELECT {', '.join(_COLS)} FROM external_skills"
    where, params = [], []
    if scope_kind is not None:
        where.append("scope_kind=?")
        params.append(scope_kind)
    if group_id is not None:
        where.append("group_id=?")
        params.append(group_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    async with _db.global_db() as db:
        async with db.execute(sql, tuple(params)) as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]


async def get_external(id: int) -> dict | None:
    async with _db.global_db() as db:
        async with db.execute(
            f"SELECT {', '.join(_COLS)} FROM external_skills WHERE id=?", (id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def remove_external(id: int) -> dict | None:
    existing = await get_external(id)
    if existing is None:
        return None
    async with _db.write_connect(_db.DB_PATH) as db:
        await db.execute("DELETE FROM external_skills WHERE id=?", (id,))
        await db.commit()
    return existing
