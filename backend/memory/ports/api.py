"""Public application ports consumed by runtime code."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from memory.contracts import ForgetMemory, MemoryEvent, ObserveMemory, RecallMemory, RecallResult


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

