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
from storage.contracts import validate_storage_port


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
    validate_storage_port(adapter)
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
    from storage.composition import current_storage_composition
    composition = current_storage_composition()
    if composition is not None:
        return composition.backend_name
    return _selected


def selected_external_adapter() -> StorageAdapter | None:
    from storage.composition import current_storage_adapter
    scoped = current_storage_adapter()
    if scoped is not None:
        return scoped
    return _adapters.get(_selected)


from storage.adapters.sqlite import SQLiteStorageAdapter
