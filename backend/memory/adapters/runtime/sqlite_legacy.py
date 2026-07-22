"""SQLite routing adapter for the host application's split databases."""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any


class LegacySQLiteMemoryDatabase:
    """Keep host-specific DB routing outside the Memory application core."""

    def __init__(self) -> None:
        self._table_presence_cache: dict[tuple[str, str], bool] = {}

    @property
    def table_presence_cache(self) -> dict[tuple[str, str], bool]:
        """Expose the shared cache for compatibility and migration tooling."""
        return self._table_presence_cache

    def clear_cache(self) -> None:
        self._table_presence_cache.clear()

    @staticmethod
    def default_db_path() -> str:
        from db import DB_PATH
        from db.context import current_db_path

        return current_db_path.get() or DB_PATH

    async def resolve_path(
        self, table_name: str, group_id: int | None
    ) -> str | None:
        from db import get_db

        default_path = self.default_db_path()
        key = (default_path, table_name)
        exists = self._table_presence_cache.get(key)
        if exists is None:
            try:
                async with get_db() as connection:
                    async with connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table_name,),
                    ) as cursor:
                        exists = (await cursor.fetchone()) is not None
            except Exception:
                # A failed probe must not silently redirect a central operation
                # into a tenant database.
                return None
            self._table_presence_cache[key] = exists
        if exists:
            return None
        if group_id is None:
            return None
        from runtime.dbpaths import group_db_path

        return group_db_path(group_id)

    async def connect(
        self, table_name: str, group_id: int | None, *, write: bool
    ) -> AbstractAsyncContextManager[Any]:
        """Return an unentered context manager for the logical Memory table."""
        from db import connect, get_db
        from db.writer import write_connect

        path = await self.resolve_path(table_name, group_id)
        if write:
            return write_connect(path if path else self.default_db_path())
        return connect(path) if path else get_db()


legacy_memory_database = LegacySQLiteMemoryDatabase()
