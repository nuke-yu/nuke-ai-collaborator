"""SQLite routing owned by the canonical Memory infrastructure."""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any

from storage import StoragePort, current_storage_adapter
from storage.adapters.sqlite import SQLiteStorageAdapter


class SQLiteMemoryDatabase:
    """Resolve Memory tables to the central or isolated group database."""

    def __init__(self, storage: StoragePort | None = None) -> None:
        self._table_presence_cache: dict[tuple[str, str], bool] = {}
        self._storage = storage or current_storage_adapter() or SQLiteStorageAdapter()

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

    @property
    def storage(self) -> StoragePort:
        return self._storage

    async def resolve_path(self, table_name: str, group_id: int | None) -> str | None:
        from db.context import current_db_path
        default_path = current_db_path.get() or self.default_db_path()
        key = (default_path, table_name)
        exists = self._table_presence_cache.get(key)
        if exists is None:
            async with self._storage.connect(default_path) as connection:
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
        path = await self.resolve_path(table_name, group_id)
        if write:
            return self._storage.write_connect(path if path else self.default_db_path())
        return self._storage.connect(path if path else self.default_db_path())
