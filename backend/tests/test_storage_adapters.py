from __future__ import annotations

import unittest
from contextlib import asynccontextmanager, contextmanager

from db.adapters import (
    StorageAdapterError,
    register_storage_adapter,
    select_storage_backend,
    selected_external_adapter,
    selected_storage_backend,
    unregister_storage_adapter,
)


class _FakeAdapter:
    name = "fake"

    @asynccontextmanager
    async def connect(self, path=None):
        yield ("read", path)

    @contextmanager
    def connect_sync(self, path=None):
        yield ("sync", path)

    @asynccontextmanager
    async def write_connect(self, path=None):
        yield ("write", path)


class StorageAdapterTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
