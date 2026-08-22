"""Infrastructure ports owned by the Memory bounded context."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import AbstractAsyncContextManager
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.domain import MemoryScope, Principal


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
class FactEnginePort(Protocol):
    async def extract_and_reconcile(self, text: str, existing: Sequence[Mapping[str, Any]], *, ai_call_fn: Any = None) -> Any: ...


@runtime_checkable
class ModelPort(Protocol):
    async def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class CaseExtractionPort(MemoryAlgorithmPort, Protocol):
    async def extract_case(self, command: Any) -> Any: ...


@runtime_checkable
class SkillExtractionPort(MemoryAlgorithmPort, Protocol):
    async def compile_candidate(self, cluster: Any) -> Any: ...


@runtime_checkable
class FailureInsightPort(MemoryAlgorithmPort, Protocol):
    async def analyze_failure(
        self,
        task: str,
        errors: Sequence[str],
        tool_records: Sequence[Mapping[str, Any]] = (),
        ai_call_fn: Any = None,
    ) -> Any: ...


@runtime_checkable
class SuccessCriticPort(MemoryAlgorithmPort, Protocol):
    async def evaluate_success(
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
        principal: Principal | None = None,
        action: str = "read",
        requesting_actor_id: str = "",
    ) -> Any: ...


@runtime_checkable
class PersonalVaultPolicyPort(Protocol):
    async def evaluate_rule(self, *, user_id: int, subject_type: str, subject_id: str,
                            object_type: str, object_id: str, action: str) -> bool | None: ...

    async def record_audit(self, *, user_id: int, actor_id: str, scope_kind: str,
                           group_id: int | None, bot_id: int | None, action: str,
                           allowed: bool, reason: str) -> None: ...


@runtime_checkable
class PersonalVaultDatabasePort(Protocol):
    def connect(self, user_id: int) -> AbstractAsyncContextManager[Any]: ...
    async def delete_vault(self, user_id: int) -> Mapping[str, Any]: ...


@runtime_checkable
class CaseClusteringPort(MemoryAlgorithmPort, Protocol):
    async def cluster(self, cases_with_timestamps: Sequence[tuple[Any, float]]) -> Any: ...


@runtime_checkable
class ContextBudgetPort(MemoryAlgorithmPort, Protocol):
    async def calculate_budget(
        self,
        max_tokens: int,
        system_prompt: str,
        working_memory: str,
        recall_memory: str,
        tool_schemas: Sequence[Mapping[str, Any]] = (),
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

    async def get_active_facts(self, as_of: float | None = None) -> Sequence[Any]: ...
    async def disambiguate_entities(self, name: str, *, limit: int = 5) -> Sequence[Any]: ...
    async def multi_hop_search(self, start_name: str, *, max_hops: int = 3,
                               as_of: float | None = None, max_paths: int = 100) -> Sequence[Any]: ...
    async def community_graph(self, as_of: float | None = None) -> Sequence[Any]: ...
    async def archive_before(self, cutoff: float, *, limit: int = 1000) -> int: ...


@runtime_checkable
class MemoryRepositoryPort(Protocol):
    async def save(self, scope: MemoryScope, records: Sequence[Mapping[str, Any]]) -> None: ...
    async def search(self, query: RecallMemory) -> Sequence[MemoryHit]: ...
    async def forget(self, scope: MemoryScope, record_ids: Sequence[str]) -> None: ...


@runtime_checkable
class ProjectionOutboxPort(Protocol):
    async def enqueue(
        self,
        connection: Any,
        *,
        event_id: str,
        projection_type: str,
        aggregate_id: str,
        aggregate_version: str,
        group_id: int,
        payload: Mapping[str, Any],
        now_ms: int | None = None,
    ) -> None: ...


@runtime_checkable
class BotMemoryProjectionReaderPort(Protocol):
    async def read_by_ids(
        self, projection_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, Any]]: ...

    async def scan_group(
        self, group_id: int, *, limit: int, offset: int = 0
    ) -> Mapping[str, Mapping[str, Any]]: ...


@runtime_checkable
class PipelineJobRepositoryPort(Protocol):
    async def enqueue(self, scope: MemoryScope, job_type: str, input_id: str, input_version: str = "1") -> str: ...
    async def reset_completed(self, scope: MemoryScope, job_id: str) -> bool: ...
    async def complete_pending_by_input(self, scope: MemoryScope, job_type: str, input_id: str, output_json: str = "{}") -> int: ...
    async def list_ready(self, scope: MemoryScope, limit: int = 10) -> Sequence[Mapping[str, Any]]: ...
    async def claim(self, scope: MemoryScope, job_id: str, lease_seconds: int = 60) -> str | None: ...
    async def renew_lease(self, scope: MemoryScope, job_id: str, lease_token: str, lease_seconds: int = 60) -> bool: ...
    async def checkpoint(self, scope: MemoryScope, thread_id: str, step_name: str,
                         state: Mapping[str, Any], parent_checkpoint_id: str | None = None) -> Mapping[str, Any]: ...
    async def latest_checkpoint(self, scope: MemoryScope, thread_id: str) -> Mapping[str, Any] | None: ...
    async def defer(self, scope: MemoryScope, job_id: str, lease_token: str) -> bool: ...
    async def complete_with_checkpoint(self, scope: MemoryScope, job_id: str, lease_token: str,
                                       output_json: str = "{}", *, thread_id: str,
                                       state: Mapping[str, Any],
                                       parent_checkpoint_id: str | None = None) -> bool: ...
    async def fail(self, scope: MemoryScope, job_id: str, lease_token: str, error_message: str) -> bool: ...
    async def stats(self, scope: MemoryScope) -> Mapping[str, int]: ...


@runtime_checkable
class MemoryDatabasePort(Protocol):
    """Resolve a logical Memory table to an isolated physical connection."""

    async def connect(
        self, table_name: str, group_id: int | None, *, write: bool
    ) -> AbstractAsyncContextManager[Any]: ...


@runtime_checkable
class MemberDirectoryPort(Protocol):
    async def get_member(self, member_id: int) -> Mapping[str, Any] | None: ...


@runtime_checkable
class MemorySecretPort(Protocol):
    def export_cursor_secret(self) -> bytes: ...


@runtime_checkable
class SkillWorkspacePort(Protocol):
    def write_skill(self, *, group_id: int, bot_id: int, name: str,
                    folder: str, content: str) -> str: ...


@runtime_checkable
class MemorySettingsPort(Protocol):
    def get(self, name: str, default: Any = None) -> Any: ...
    def is_missing_schema_error(self, error: BaseException) -> bool: ...


@runtime_checkable
class ProjectionDeliveryPort(Protocol):
    """Deliver a durable canonical-memory event to a derived projection."""

    async def deliver(
        self, projection_type: str, payload: Mapping[str, Any]
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectionDrainResult:
    claimed: int = 0
    completed: int = 0
    failed: int = 0


@runtime_checkable
class ProjectionReconcilerPort(Protocol):
    """Rebuild projection intents from canonical state for one tenant."""

    async def reconcile(self, group_id: int) -> int: ...


@runtime_checkable
class MemorySchemaPort(Protocol):
    """Initialize or upgrade Memory-owned storage for one tenant."""

    async def ensure_group(self, group_id: int) -> int: ...
