from __future__ import annotations

import unittest
from contextlib import asynccontextmanager, contextmanager
from tempfile import NamedTemporaryFile

from db.adapters import (
    StorageAdapterError,
    register_storage_adapter,
    select_storage_backend,
    selected_external_adapter,
    selected_storage_backend,
    unregister_storage_adapter,
)
from storage.adapters import SQLiteDialect


class _FakeAdapter:
    name = "fake"
    dialect = SQLiteDialect()

    @asynccontextmanager
    async def connect(self, path=None):
        yield ("read", path)

    @contextmanager
    def connect_sync(self, path=None):
        yield ("sync", path)

    @asynccontextmanager
    async def write_connect(self, path=None):
        yield ("write", path)

    async def migrate(self, path=None, migration=None):
        return None

    async def health_check(self, path=None):
        return {"backend": self.name, "healthy": True}

    async def close(self):
        return None


class StorageAdapterTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        select_storage_backend("sqlite")
        try:
            unregister_storage_adapter("fake")
        except StorageAdapterError:
            pass

    def test_registered_adapter_is_selectable_without_sqlite_fallback(self) -> None:
        adapter = _FakeAdapter()
        register_storage_adapter("fake", adapter)
        select_storage_backend("fake")
        self.assertEqual(selected_storage_backend(), "fake")
        self.assertIs(selected_external_adapter(), adapter)

    def test_unregistered_backend_fails_closed(self) -> None:
        with self.assertRaises(StorageAdapterError):
            select_storage_backend("postgres")
        self.assertEqual(selected_storage_backend(), "sqlite")

    def test_registry_rejects_incomplete_adapter(self) -> None:
        with self.assertRaises(TypeError):
            register_storage_adapter("fake", object())

    async def test_sqlite_adapter_exposes_complete_storage_port(self) -> None:
        from db.adapters import SQLiteStorageAdapter

        adapter = SQLiteStorageAdapter()
        for method in ("connect", "connect_sync", "write_connect", "migrate", "health_check", "close"):
            self.assertTrue(callable(getattr(adapter, method)))

    async def test_sqlite_adapter_health_and_migration(self) -> None:
        from db.adapters import SQLiteStorageAdapter

        adapter = SQLiteStorageAdapter()
        with NamedTemporaryFile(suffix=".db") as file:
            health = await adapter.health_check(file.name)
            self.assertEqual(health, {"backend": "sqlite", "healthy": True})

            async def migration(connection):
                await connection.execute("CREATE TABLE storage_contract_probe (id INTEGER)")

            await adapter.migrate(file.name, migration)
            async with adapter.connect(file.name) as connection:
                cursor = await connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'storage_contract_probe'"
                )
                self.assertIsNotNone(await cursor.fetchone())

    async def test_sqlite_writer_is_serialized_and_facade_independent(self) -> None:
        from storage.adapters.sqlite import SQLiteStorageAdapter

        adapter = SQLiteStorageAdapter()
        with NamedTemporaryFile(suffix=".db") as file:
            async with adapter.write_connect(file.name) as connection:
                await connection.execute("CREATE TABLE writer_probe (value TEXT)")
                await connection.execute("INSERT INTO writer_probe VALUES ('ok')")
                await connection.commit()
            async with adapter.connect(file.name) as connection:
                cursor = await connection.execute("SELECT value FROM writer_probe")
                self.assertEqual(await cursor.fetchone(), ("ok",))
            await adapter.close()


if __name__ == "__main__":
    unittest.main()
