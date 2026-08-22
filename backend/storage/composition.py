"""Context-scoped storage composition for embedded and multi-tenant hosts."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .ports import StoragePort


@dataclass(frozen=True)
class StorageComposition:
    """One storage backend binding owned by a host composition."""

    backend_name: str = "sqlite"
    adapter: StoragePort | None = None

    def __post_init__(self) -> None:
        normalized = self.backend_name.strip().lower()
        if not normalized:
            raise ValueError("storage backend name is required")
        object.__setattr__(self, "backend_name", normalized)
        if normalized == "sqlite" and self.adapter is not None:
            raise ValueError("sqlite composition uses the built-in adapter")
        if self.adapter is not None:
            missing = [
                method for method in (
                    "connect", "connect_sync", "write_connect",
                    "migrate", "health_check", "close",
                ) if not callable(getattr(self.adapter, method, None))
            ]
            if missing:
                raise TypeError(
                    f"storage adapter is missing capabilities: {', '.join(missing)}"
                )


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
