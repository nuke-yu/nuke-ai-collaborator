"""Storage adapter registry.

SQLite remains the built-in implementation.  Alternative backends must provide
both read and serialized-write context managers before configuration can select
them; naming an unregistered backend is a startup error, never a silent
fallback to SQLite.
"""
from __future__ import annotations

from storage.ports import (
    ConnectionPort,
    HealthCheckPort,
    LifecyclePort,
    MigrationPort,
    StoragePort,
    TransactionPort,
)


# Compatibility name used by existing adapter registration callers.
StorageAdapter = StoragePort


class StorageAdapterError(RuntimeError):
    pass


_adapters: dict[str, StorageAdapter] = {}
_selected = "sqlite"


def register_storage_adapter(name: str, adapter: StorageAdapter) -> None:
    normalized = name.strip().lower() if isinstance(name, str) else ""
    if not normalized or normalized == "sqlite":
        raise ValueError("external storage adapter name is required")
    if getattr(adapter, "name", normalized).lower() != normalized:
        raise ValueError("storage adapter name does not match registration name")
    for method in (
        "connect", "connect_sync", "write_connect", "migrate", "health_check", "close",
    ):
        if not callable(getattr(adapter, method, None)):
            raise TypeError(f"storage adapter must implement {method}()")
    _adapters[normalized] = adapter


def unregister_storage_adapter(name: str) -> None:
    if name.lower() == _selected:
        raise StorageAdapterError("cannot unregister the selected storage adapter")
    _adapters.pop(name.lower(), None)


def select_storage_backend(name: str) -> None:
    global _selected
    normalized = name.strip().lower() if isinstance(name, str) else ""
    if normalized == "sqlite":
        _selected = normalized
        return
    if normalized not in _adapters:
        raise StorageAdapterError(
            f"storage backend {normalized!r} is not registered; refusing SQLite fallback"
        )
    _selected = normalized


def selected_storage_backend() -> str:
    return _selected


def selected_external_adapter() -> StorageAdapter | None:
    return _adapters.get(_selected)


class SQLiteStorageAdapter:
    """Concrete capability object for the built-in SQLite implementation.

    The legacy ``db`` module remains the routing facade; this adapter exposes
    the same backend's complete port for composition roots and contract tests.
    """

    name = "sqlite"

    def connect(self, path: str | None = None):
        from db import connect
        return connect(path)

    def connect_sync(self, path: str | None = None):
        from db import connect_sync
        return connect_sync(path)

    def write_connect(self, path: str | None = None):
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
