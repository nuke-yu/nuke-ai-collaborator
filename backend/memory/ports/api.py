"""Public application ports consumed by runtime code."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from memory.contracts import (CreatePersonalProjection, CreatePersonalRecord, ForgetMemory,
                              IngestPersonalKnowledge, MemoryEvent, ObserveMemory,
                              ObservePersonalHabit, ProcessLearningCase, RecallMemory,
                              RecallResult)
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
class LearningPort(Protocol):
    async def process_case(self, command: ProcessLearningCase) -> str: ...


@runtime_checkable
class PersonalKnowledgePort(Protocol):
    async def create_record(self, command: CreatePersonalRecord) -> str: ...
    async def create_projection(self, command: CreatePersonalProjection) -> str: ...
    async def ingest(self, command: IngestPersonalKnowledge) -> str: ...
    async def observe_habit(self, command: ObservePersonalHabit) -> str: ...
    async def rebuild(self, scope: MemoryScope) -> Mapping[str, Any]: ...
    async def export(self, scope: MemoryScope) -> Mapping[str, Any]: ...
    async def delete(self, scope: MemoryScope) -> bool: ...
