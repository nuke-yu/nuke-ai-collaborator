"""Shared FastAPI dependencies for group-scoped routes."""
from db import ensure_group_db_ready
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
