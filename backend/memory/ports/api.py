"""Public application ports consumed by runtime code."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from memory.contracts import ForgetMemory, MemoryEvent, ObserveMemory, RecallMemory, RecallResult
from memory.domain import MemoryScope


@runtime_checkable
class MemoryCommandPort(Protocol):
    async def observe(self, command: ObserveMemory) -> None: ...
    async def forget(self, command: ForgetMemory) -> None: ...


@runtime_checkable
class MemoryQueryPort(Protocol):
    async def recall(self, query: RecallMemory) -> RecallResult: ...


@runtime_checkable
class MemoryEventPort(Protocol):
    async def publish(self, event: MemoryEvent) -> None: ...


@runtime_checkable
class PersonalKnowledgePort(Protocol):
    async def export(self, scope: MemoryScope) -> Mapping[str, Any]: ...
    async def delete(self, scope: MemoryScope) -> bool: ...
