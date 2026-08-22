from __future__ import annotations

import unittest
from contextlib import asynccontextmanager, contextmanager

from storage import StoragePort


class _ContractProbe:
    name = "probe"

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


class StoragePortsTest(unittest.TestCase):
    def test_contract_is_available_without_importing_db_facade(self) -> None:
        probe = _ContractProbe()
        self.assertTrue(isinstance(probe, StoragePort))


if __name__ == "__main__":
    unittest.main()
