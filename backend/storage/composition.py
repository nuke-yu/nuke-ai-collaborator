"""Context-scoped storage composition for embedded and multi-tenant hosts."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .ports import StoragePort
from .contracts import validate_storage_port
from .adapters.sqlite import SQLiteStorageAdapter


@dataclass(frozen=True)
class StorageComposition:
    """One storage backend binding owned by a host composition."""

    backend_name: str = "sqlite"
    adapter: StoragePort = field(default_factory=SQLiteStorageAdapter)

    def __post_init__(self) -> None:
        normalized = self.backend_name.strip().lower()
        if not normalized:
            raise ValueError("storage backend name is required")
        object.__setattr__(self, "backend_name", normalized)
        validate_storage_port(self.adapter)


_current: ContextVar[StorageComposition | None] = ContextVar(
    "storage_composition", default=None
)


def current_storage_composition() -> StorageComposition | None:
    return _current.get()


def current_storage_adapter() -> StoragePort | None:
    composition = _current.get()
    return composition.adapter if composition is not None else None


@contextmanager
def storage_scope(composition: StorageComposition):
    token = _current.set(composition)
    try:
        yield composition
    finally:
        _current.reset(token)
