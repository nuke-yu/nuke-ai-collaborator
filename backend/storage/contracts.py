"""Runtime validation shared by storage composition roots."""
from __future__ import annotations

from .ports import StoragePort


REQUIRED_STORAGE_CAPABILITIES = (
    "connect",
    "connect_sync",
    "write_connect",
    "migrate",
    "health_check",
    "close",
)


def missing_storage_capabilities(adapter: object) -> tuple[str, ...]:
    return tuple(
        capability for capability in REQUIRED_STORAGE_CAPABILITIES
        if not callable(getattr(adapter, capability, None))
    )


def validate_storage_port(adapter: object) -> StoragePort:
    missing = missing_storage_capabilities(adapter)
    if missing:
        raise TypeError(
            f"storage adapter is missing capabilities: {', '.join(missing)}"
        )
    return adapter  # type: ignore[return-value]
