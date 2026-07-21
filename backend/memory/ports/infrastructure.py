"""Infrastructure ports owned by the Memory bounded context."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.domain import MemoryScope


@dataclass(frozen=True, slots=True)
class AlgorithmDescriptor:
    algorithm_id: str
    source: str
    version: str
    license: str
    capabilities: tuple[str, ...]


@runtime_checkable
class MemoryAlgorithmPort(Protocol):
    descriptor: AlgorithmDescriptor

    async def extract(self, command: ObserveMemory) -> Sequence[Mapping[str, Any]]: ...
    async def retrieve(self, query: RecallMemory) -> Sequence[MemoryHit]: ...


@runtime_checkable
class MemoryRepositoryPort(Protocol):
    async def save(self, scope: MemoryScope, records: Sequence[Mapping[str, Any]]) -> None: ...
    async def search(self, query: RecallMemory) -> Sequence[MemoryHit]: ...
    async def forget(self, scope: MemoryScope, record_ids: Sequence[str]) -> None: ...

