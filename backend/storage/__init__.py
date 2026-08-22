"""Standalone storage contracts and composition primitives."""

from .ports import (
    ConnectionPort,
    HealthCheckPort,
    LifecyclePort,
    MigrationPort,
    StoragePort,
    TransactionPort,
)
from .composition import (
    StorageComposition,
    current_storage_adapter,
    current_storage_composition,
    storage_scope,
)

__all__ = [
    "ConnectionPort",
    "HealthCheckPort",
    "LifecyclePort",
    "MigrationPort",
    "StoragePort",
    "TransactionPort",
    "StorageComposition",
    "current_storage_adapter",
    "current_storage_composition",
    "storage_scope",
]
