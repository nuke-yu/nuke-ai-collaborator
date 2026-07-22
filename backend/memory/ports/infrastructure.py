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


@runtime_checkable
class FactExtractionPort(MemoryAlgorithmPort, Protocol):
    async def extract(self, command: ObserveMemory) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class FailureInsightPort(MemoryAlgorithmPort, Protocol):
    async def analyze_failure(self, task: str, error_traces: Sequence[str]) -> Any: ...


@runtime_checkable
class SuccessCriticPort(MemoryAlgorithmPort, Protocol):
    async def evaluate(
        self,
        task: str,
        outcome: str,
        tool_records: Sequence[Mapping[str, Any]] = (),
        error_traces: Sequence[str] = (),
        ai_call_fn: Any = None,
    ) -> Any: ...


@runtime_checkable
class RerankPort(MemoryAlgorithmPort, Protocol):
    async def rerank(
        self,
        keyword_hits: Sequence[Mapping[str, Any]],
        vector_hits: Sequence[Mapping[str, Any]],
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class DAGCheckpointPort(MemoryAlgorithmPort, Protocol):
    async def checkpoint(
        self,
        thread_id: str,
        step_name: str,
        state: Mapping[str, Any],
        parent_id: str | None = None,
    ) -> Any: ...


@runtime_checkable
class MemoryACLPort(MemoryAlgorithmPort, Protocol):
    async def check_acl(
        self,
        scope: MemoryScope,
        requesting_actor_id: str,
        action: str = "read",
        actor_group_ids: Sequence[int] | set[int] = (),
    ) -> Any: ...


@runtime_checkable
class TemporalGraphPort(MemoryAlgorithmPort, Protocol):
    async def add_temporal_fact(
        self,
        source: str,
        relation: str,
        target: str,
        fact: str,
        valid_at: float | None = None,
    ) -> Any: ...


@runtime_checkable
class MemoryRepositoryPort(Protocol):
    async def save(self, scope: MemoryScope, records: Sequence[Mapping[str, Any]]) -> None: ...
    async def search(self, query: RecallMemory) -> Sequence[MemoryHit]: ...
    async def forget(self, scope: MemoryScope, record_ids: Sequence[str]) -> None: ...


@runtime_checkable
class PipelineJobRepositoryPort(Protocol):
    async def enqueue(self, scope: MemoryScope, job_type: str, input_id: str, input_version: str = "1") -> str: ...
    async def claim(self, scope: MemoryScope, job_id: str, lease_seconds: int = 60) -> str | None: ...
    async def complete(self, scope: MemoryScope, job_id: str, output_json: str = "{}", lease_token: str | None = None) -> bool: ...
    async def fail(self, scope: MemoryScope, job_id: str, error_message: str, max_attempts: int = 3, lease_token: str | None = None) -> bool: ...


