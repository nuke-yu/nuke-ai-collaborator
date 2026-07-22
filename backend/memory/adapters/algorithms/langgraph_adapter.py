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
        capabilities=("dag_checkpoint", "state_persistence", "worker_recovery"),
    )

    def __init__(self, engine: LangGraphDAGEngine | None = None) -> None:
        self._engine = engine or LangGraphDAGEngine()

    async def checkpoint(
        self,
        thread_id: str,
        step_name: str,
        state: Mapping[str, Any],
        parent_id: str | None = None,
    ) -> DAGStateCheckpoint:
        """Create stateful DAG checkpoint."""
        return self._engine.create_checkpoint(thread_id, step_name, state, parent_id)
