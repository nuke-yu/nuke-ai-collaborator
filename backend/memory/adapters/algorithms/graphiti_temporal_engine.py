"""Graphiti SQLite Temporal Graph & Invalidation Engine (Apache-2.0 ported algorithm).

Ported from Graphiti (Zep AI / Apache-2.0) Temporal Knowledge Graph:
- Bi-temporal Knowledge Graph nodes & edges with valid_at and invalid_at timestamps.
- Invalidate conflicting edges upon new state observation (set invalid_at = current_time) while preserving immutable history.
- Enable point-in-time active edge reconstruction as_of(timestamp).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class TemporalEntityNode:
    node_id: str
    name: str
    entity_type: str
    created_at: float


@dataclass(slots=True)
class TemporalEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation: str
    fact_statement: str
    valid_at: float
    invalid_at: float | None = None


class GraphitiTemporalEngine:
    """Audit-grade Graphiti Bi-Temporal Knowledge Graph & Invalidation Engine."""

    def __init__(self) -> None:
        self._nodes: dict[str, TemporalEntityNode] = {}
        self._edges: list[TemporalEdge] = []

    def get_or_create_node(self, name: str, entity_type: str = "concept") -> TemporalEntityNode:
        """Retrieve or register temporal entity node."""
        node_id = f"node:{entity_type}:{name.lower().strip()}"
        if node_id not in self._nodes:
            self._nodes[node_id] = TemporalEntityNode(
                node_id=node_id,
                name=name.strip(),
                entity_type=entity_type,
                created_at=time.time(),
            )
        return self._nodes[node_id]

    def add_edge(
        self,
        source_name: str,
        relation: str,
        target_name: str,
        fact_statement: str,
        valid_at: float | None = None,
    ) -> TemporalEdge:
        """Add temporal edge and automatically invalidate conflicting active edges."""
        now = time.time() if valid_at is None else valid_at
        src_node = self.get_or_create_node(source_name)
        tgt_node = self.get_or_create_node(target_name)

        # Invalidate prior active edges with same source and relation
        self.invalidate_conflicting_edges(
            source_node_id=src_node.node_id,
            relation=relation,
            invalid_at=now,
        )

        edge_id = f"edge:{src_node.node_id}:{relation}:{tgt_node.node_id}:{int(now)}"
        edge = TemporalEdge(
            edge_id=edge_id,
            source_node_id=src_node.node_id,
            target_node_id=tgt_node.node_id,
            relation=relation,
            fact_statement=fact_statement,
            valid_at=now,
            invalid_at=None,
        )
        self._edges.append(edge)
        return edge

    def invalidate_conflicting_edges(
        self,
        source_node_id: str,
        relation: str,
        invalid_at: float | None = None,
    ) -> list[TemporalEdge]:
        """Set invalid_at timestamp on active conflicting edges."""
        now = time.time() if invalid_at is None else invalid_at
        invalidated: list[TemporalEdge] = []

        for edge in self._edges:
            if (
                edge.source_node_id == source_node_id
                and edge.relation == relation
                and edge.invalid_at is None
            ):
                edge.invalid_at = now
                invalidated.append(edge)

        return invalidated

    def get_active_edges(self, as_of: float | None = None) -> list[TemporalEdge]:
        """Query active temporal edges as of a specific point in time."""
        query_time = time.time() if as_of is None else as_of
        active: list[TemporalEdge] = []

        for edge in self._edges:
            if edge.valid_at <= query_time:
                if edge.invalid_at is None or edge.invalid_at > query_time:
                    active.append(edge)

        return active
