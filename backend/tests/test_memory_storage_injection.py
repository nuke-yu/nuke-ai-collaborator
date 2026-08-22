from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from memory.infrastructure import SQLiteMemoryDatabase
from storage import StorageComposition, storage_scope
from storage.adapters import SQLiteDialect


class _StorageProbe:
    name = "probe"
    dialect = SQLiteDialect()

    def __init__(self):
        self.paths: list[str] = []

    @asynccontextmanager
    async def connect(self, path=None):
        self.paths.append(str(path))
        yield _ConnectionProbe()

    def connect_sync(self, path=None):
        raise NotImplementedError

    def write_connect(self, path=None):
        raise NotImplementedError

    async def migrate(self, path=None, migration=None):
        return None

    async def health_check(self, path=None):
        return {"backend": self.name, "healthy": True}

    async def close(self):
        return None


class _ConnectionProbe:
    def execute(self, *_args, **_kwargs):
        raise NotImplementedError


class MemoryStorageInjectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_memory_database_uses_scoped_storage_adapter(self) -> None:
        probe = _StorageProbe()
        with storage_scope(StorageComposition("probe", probe)):
            database = SQLiteMemoryDatabase()
        self.assertIs(database.storage, probe)

    async def test_explicit_storage_wins_over_ambient_composition(self) -> None:
        ambient = _StorageProbe()
        explicit = _StorageProbe()
        with storage_scope(StorageComposition("ambient", ambient)):
            database = SQLiteMemoryDatabase(explicit)
        self.assertIs(database.storage, explicit)


if __name__ == "__main__":
    unittest.main()
