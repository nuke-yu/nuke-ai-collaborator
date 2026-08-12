"""SQLite routing owned by the canonical Memory infrastructure."""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any


class SQLiteMemoryDatabase:
    """Resolve Memory tables to the central or isolated group database."""

    def __init__(self) -> None:
        self._table_presence_cache: dict[tuple[str, str], bool] = {}

    @staticmethod
    def default_db_path() -> str:
        from db import DB_PATH
        from db.context import current_db_path
        return current_db_path.get() or DB_PATH

    def clear_cache(self) -> None:
        self._table_presence_cache.clear()

    @property
    def table_presence_cache(self) -> dict[tuple[str, str], bool]:
        return self._table_presence_cache

    async def resolve_path(self, table_name: str, group_id: int | None) -> str | None:
        from db import get_db
        from db.context import current_db_path
        default_path = current_db_path.get() or self.default_db_path()
        key = (default_path, table_name)
        exists = self._table_presence_cache.get(key)
        if exists is None:
            async with get_db() as connection:
                async with connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ) as cursor:
                    exists = (await cursor.fetchone()) is not None
            self._table_presence_cache[key] = exists
        if exists or group_id is None:
            return None
        from workspace import layout

        return str(layout.group_dir(group_id) / "chat.db")

    async def connect(
        self, table_name: str, group_id: int | None, *, write: bool
    ) -> AbstractAsyncContextManager[Any]:
        from db import connect, get_db
        from db.writer import write_connect
        from db.context import current_db_path
        from db import DB_PATH

        path = await self.resolve_path(table_name, group_id)
        if write:
            return write_connect(path if path else self.default_db_path())
        return connect(path) if path else get_db()
