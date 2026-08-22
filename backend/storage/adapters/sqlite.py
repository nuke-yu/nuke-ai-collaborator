"""SQLite implementation of the storage ports."""
from __future__ import annotations

import sqlite3
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager

import aiosqlite

from storage.ports import StoragePort


class SQLiteStorageAdapter(StoragePort):
    name = "sqlite"

    @asynccontextmanager
    async def connect(self, path: str | None = None) -> AbstractAsyncContextManager:
        if path is None:
            raise ValueError("SQLite connection path is required")
        conn = aiosqlite.connect(path)
        conn.daemon = True
        conn = await conn
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            await conn.close()

    @contextmanager
    def connect_sync(self, path: str | None = None) -> AbstractContextManager:
        if path is None:
            raise ValueError("SQLite connection path is required")
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def write_connect(self, path: str | None = None) -> AbstractAsyncContextManager:
        if path is None:
            raise ValueError("SQLite writer path is required")
        from db.writer import write_connect

        return write_connect(path)

    async def migrate(self, path=None, migration=None) -> None:
        if migration is None:
            return
        async with self.write_connect(path) as connection:
            await migration(connection)
            await connection.commit()

    async def health_check(self, path=None) -> dict[str, object]:
        async with self.connect(path) as connection:
            await connection.execute("SELECT 1")
        return {"backend": self.name, "healthy": True}

    async def close(self) -> None:
        from db import aclose_writer

        await aclose_writer()
