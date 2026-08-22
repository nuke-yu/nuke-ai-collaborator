"""SQLite implementation of the storage ports."""
from __future__ import annotations

import sqlite3
import asyncio
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager

import aiosqlite

from storage.ports import StoragePort


class SQLiteStorageAdapter(StoragePort):
    name = "sqlite"
    _writers: dict[tuple[int, str], tuple[asyncio.Lock, aiosqlite.Connection | None]] = {}

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
        return self._write_connect(path)

    @asynccontextmanager
    async def _write_connect(self, path: str):
        loop_id = id(asyncio.get_running_loop())
        key = (loop_id, path)
        state = self._writers.get(key)
        if state is None:
            state = (asyncio.Lock(), None)
            self._writers[key] = state
        lock, connection = state
        async with lock:
            if connection is None:
                connection = aiosqlite.connect(path)
                connection.daemon = True
                connection = await connection
                await connection.execute("PRAGMA journal_mode=WAL")
                await connection.execute("PRAGMA busy_timeout=5000")
                await connection.execute("PRAGMA foreign_keys=ON")
                self._writers[key] = (lock, connection)
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise

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
        loop_id = id(asyncio.get_running_loop())
        for key in [key for key in self._writers if key[0] == loop_id]:
            _lock, connection = self._writers.pop(key)
            if connection is not None:
                await connection.close()
