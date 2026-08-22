from __future__ import annotations

import unittest
from contextlib import asynccontextmanager, contextmanager

from db.adapters import selected_external_adapter, selected_storage_backend
from storage import StorageComposition, storage_scope


class _Adapter:
    def __init__(self, name: str):
        self.name = name

    @asynccontextmanager
    async def connect(self, path=None):
        yield path

    @contextmanager
    def connect_sync(self, path=None):
        yield path

    @asynccontextmanager
    async def write_connect(self, path=None):
        yield path

    async def migrate(self, path=None, migration=None):
        return None

    async def health_check(self, path=None):
        return {"backend": self.name, "healthy": True}

    async def close(self):
        return None


class StorageCompositionTest(unittest.TestCase):
    def test_nested_compositions_restore_previous_binding(self) -> None:
        first = _Adapter("first")
        second = _Adapter("second")
        with storage_scope(StorageComposition("first", first)):
            self.assertEqual(selected_storage_backend(), "first")
            self.assertIs(selected_external_adapter(), first)
            with storage_scope(StorageComposition("second", second)):
                self.assertEqual(selected_storage_backend(), "second")
                self.assertIs(selected_external_adapter(), second)
            self.assertEqual(selected_storage_backend(), "first")
            self.assertIs(selected_external_adapter(), first)

    def test_sqlite_scope_does_not_use_external_adapter(self) -> None:
        with storage_scope(StorageComposition()):
            self.assertEqual(selected_storage_backend(), "sqlite")
            self.assertIsNone(selected_external_adapter())


if __name__ == "__main__":
    unittest.main()
