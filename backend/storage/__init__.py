"""Standalone storage contracts and composition primitives."""

from .ports import (
    ConnectionPort,
    HealthCheckPort,
    LifecyclePort,
    MigrationPort,
    StorageDialectPort,
    StoragePort,
    TransactionPort,
)
from .composition import (
    StorageComposition,
    current_storage_adapter,
    current_storage_composition,
    storage_scope,
)
from .contracts import (
    REQUIRED_STORAGE_CAPABILITIES,
    missing_storage_capabilities,
    validate_storage_port,
)

__all__ = [
    "ConnectionPort",
    "HealthCheckPort",
    "LifecyclePort",
    "MigrationPort",
    "StoragePort",
    "StorageDialectPort",
    "TransactionPort",
    "StorageComposition",
    "current_storage_adapter",
    "current_storage_composition",
    "storage_scope",
    "REQUIRED_STORAGE_CAPABILITIES",
    "missing_storage_capabilities",
    "validate_storage_port",
]
