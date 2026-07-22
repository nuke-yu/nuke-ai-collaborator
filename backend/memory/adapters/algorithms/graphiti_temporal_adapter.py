"""Graphiti Temporal Graph Algorithm Adapter implementing MemoryAlgorithmPort."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.adapters.algorithms.graphiti_temporal_engine import (
    GraphitiTemporalEngine, TemporalEdge)
from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.ports.infrastructure import AlgorithmDescriptor, MemoryAlgorithmPort


class GraphitiTemporalAlgorithmAdapter:
    """Adapter wrapping Graphiti Bi-Temporal Knowledge Graph and Invalidation Engine."""

    descriptor = AlgorithmDescriptor(
        algorithm_id="nuke.graphiti.temporal_graph",
        source="Graphiti (Zep AI / Apache-2.0)",
        version="v0.3",
        license="Apache-2.0",
        capabilities=("temporal_graph", "bitemporal_invalidation", "invalid_at_timestamp"),
    )

    def __init__(self, engine: GraphitiTemporalEngine | None = None) -> None:
        self._engine = engine or GraphitiTemporalEngine()

    async def add_temporal_fact(
        self,
        source: str,
        relation: str,
        target: str,
        fact: str,
        valid_at: float | None = None,
    ) -> TemporalEdge:
        """Add temporal fact and invalidate active conflicting edges."""
        return self._engine.add_edge(source, relation, target, fact, valid_at=valid_at)

    async def get_active_facts(self, as_of: float | None = None) -> Sequence[TemporalEdge]:
        """Retrieve active temporal facts as of point in time."""
        return self._engine.get_active_edges(as_of=as_of)

    async def extract(self, command: ObserveMemory) -> Sequence[Mapping[str, Any]]:
        """Placeholder for observation extraction."""
        return ()

    async def retrieve(self, query: RecallMemory) -> Sequence[MemoryHit]:
        """Placeholder for temporal graph retrieval."""
        return ()
