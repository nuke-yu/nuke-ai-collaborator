"""LangGraph DAG Checkpoint Algorithm Adapter implementing MemoryAlgorithmPort."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.adapters.algorithms.langgraph_dag_engine import (
    DAGStateCheckpoint, LangGraphDAGEngine)
from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.ports.infrastructure import AlgorithmDescriptor, MemoryAlgorithmPort


class LangGraphDAGAlgorithmAdapter:
    """Adapter wrapping LangGraph Stateful Execution DAG Checkpoints."""

    descriptor = AlgorithmDescriptor(
        algorithm_id="nuke.langgraph.dag_checkpoint",
        source="LangGraph (LangChain / MIT)",
        version="v0.2",
        license="MIT",
        capabilities=("dag_checkpoint", "state_persistence", "worker_recovery", "checkpoint_saver_compat"),
    )

    def __init__(self, engine: LangGraphDAGEngine | None = None) -> None:
        self._engine = engine or LangGraphDAGEngine()
        self._saver: dict[str, DAGStateCheckpoint] = {}
        self._pending_writes: dict[str, list[dict[str, Any]]] = {}

    async def checkpoint(
        self,
        thread_id: str,
        step_name: str,
        state: Mapping[str, Any],
        parent_id: str | None = None,
    ) -> DAGStateCheckpoint:
        """Create stateful DAG checkpoint."""
        return self._engine.create_checkpoint(thread_id, step_name, state, parent_id)

    async def put(self, thread_id: str, step_name: str, state: Mapping[str, Any], parent_id: str | None = None):
        checkpoint = await self.checkpoint(thread_id, step_name, state, parent_id)
        self._saver[checkpoint.checkpoint_id] = checkpoint
        return checkpoint

    async def get_tuple(self, thread_id: str) -> DAGStateCheckpoint | None:
        candidates = [item for item in self._saver.values() if item.thread_id == thread_id]
        return max(candidates, key=lambda item: item.created_at, default=None)

    async def put_writes(self, checkpoint_id: str, task_id: str, writes: Mapping[str, Any]) -> None:
        self._pending_writes.setdefault(checkpoint_id, [])
        for channel, value in writes.items():
            self._pending_writes[checkpoint_id].append(
                {"task_id": task_id, "channel": str(channel), "value": value}
            )

    async def pending_writes(self, checkpoint_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(self._pending_writes.get(checkpoint_id, ()))

    async def delete_thread(self, thread_id: str) -> int:
        ids = [key for key, value in self._saver.items() if value.thread_id == thread_id]
        for key in ids:
            self._saver.pop(key, None)
            self._pending_writes.pop(key, None)
        return len(ids)
