"""Shared FastAPI dependencies for group-scoped routes."""
from fastapi import Depends, HTTPException

from core import auth
from db import ensure_group_db_ready, global_db
from runtime.dbpaths import group_db_path


async def ensure_group_ready(group_id: int | None = None) -> None:
    """Route-layer guard: any HTTP handler that binds a group's private DB in the main
    process must first ensure that DB exists and is schema-migrated. Worker hydration runs
    migrations, but HTTP handlers don't go through hydration — without this a request to a
    not-yet-hydrated group hits "no such column/table" on a freshly-added migration.

    Attach with `dependencies=[Depends(ensure_group_ready)]` so the guarantee lives at the
    route layer and can't be forgotten inside a handler body. `group_id` resolves from the
    same path/query param the endpoint declares; it is optional so routes that take group_id
    as an optional query param don't break when it is omitted.
    """
    if group_id is None:
        return
    await ensure_group_db_ready(group_db_path(group_id))


async def require_group_member(
    group_id: int,
    user: dict = Depends(auth.get_current_user),
) -> dict:
    """Authorize a human against central membership without opening the group DB."""
    async with global_db() as conn:
        async with conn.execute(
            "SELECT 1 FROM group_memberships WHERE user_id = ? AND group_id = ?",
            (int(user["uid"]), group_id),
        ) as cur:
            membership = await cur.fetchone()
    if membership is None:
        # Do not reveal whether a non-member group exists.
        raise HTTPException(status_code=404, detail="Group not found")
    return user


async def require_group_member_ready(
    group_id: int,
    user: dict = Depends(require_group_member),
) -> dict:
    """Authorize first, then initialize the one private DB the route may read."""
    await ensure_group_db_ready(group_db_path(group_id))
    return user


async def require_group_owner(
    group_id: int,
    user: dict = Depends(auth.get_current_user),
) -> dict:
    """Restrict integration configuration to the Group owner."""
    async with global_db() as conn:
        async with conn.execute(
            "SELECT role FROM group_memberships WHERE user_id=? AND group_id=?",
            (int(user["uid"]), group_id),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if row[0] != "owner":
        raise HTTPException(status_code=403, detail="Group owner privileges required")
    await ensure_group_db_ready(group_db_path(group_id))
    return user
