"""Dependency-inversion contracts for replaceable storage backends."""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Awaitable, Callable, Protocol, runtime_checkable


class ConnectionPort(Protocol):
    name: str

    def connect(self, path: str | None = None) -> AbstractAsyncContextManager:
        ...

    def connect_sync(self, path: str | None = None) -> AbstractContextManager:
        ...


class TransactionPort(Protocol):
    """Serialized write transaction capability."""

    def write_connect(self, path: str | None = None) -> AbstractAsyncContextManager:
        ...


class MigrationPort(Protocol):
    async def migrate(
        self,
        path: str | None = None,
        migration: Callable[[object], Awaitable[None]] | None = None,
    ) -> None:
        ...


class HealthCheckPort(Protocol):
    async def health_check(self, path: str | None = None) -> dict[str, object]:
        ...


class LifecyclePort(Protocol):
    async def close(self) -> None:
        ...


@runtime_checkable
class StoragePort(
    ConnectionPort,
    TransactionPort,
    MigrationPort,
    HealthCheckPort,
    LifecyclePort,
    Protocol,
):
    """Complete storage boundary required by a replaceable backend."""
