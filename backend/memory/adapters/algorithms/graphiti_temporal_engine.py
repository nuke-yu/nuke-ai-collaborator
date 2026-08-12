"""Graphiti SQLite Temporal Graph & Invalidation Engine (Apache-2.0 ported algorithm).

Ported from Graphiti (Zep AI / Apache-2.0) Temporal Knowledge Graph:
- Bi-temporal Knowledge Graph nodes & edges with valid_at and invalid_at timestamps.
- Invalidate conflicting edges upon new state observation (set invalid_at = current_time) while preserving immutable history.
- Enable point-in-time active edge reconstruction as_of(timestamp).
"""
from __future__ import annotations

import time
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence


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

    def __init__(self, *, rrf_k: int = 60) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        self._nodes: dict[str, TemporalEntityNode] = {}
        self._edges: list[TemporalEdge] = []
        self._aliases: dict[str, str] = {}
        self.rrf_k = rrf_k

    @staticmethod
    def normalize_entity_name(name: str) -> str:
        """Normalize an entity key while keeping display text untouched."""
        value = re.sub(r"\s+", " ", (name or "").strip().lower())
        value = value.strip(".,:;!?()[]{}<>\"'")
        return value

    def register_alias(self, alias: str, canonical_name: str) -> TemporalEntityNode:
        """Register an explicit alias and return the canonical entity node."""
        canonical = self.normalize_entity_name(canonical_name)
        alias_key = self.normalize_entity_name(alias)
        if not canonical or not alias_key:
            raise ValueError("entity alias and canonical name are required")
        node = self.get_or_create_node(canonical_name)
        self._aliases[alias_key] = node.node_id
        return node

    def resolve_entity(self, name: str) -> TemporalEntityNode | None:
        """Resolve a normalized name or registered alias without creating data."""
        key = self.normalize_entity_name(name)
        node_id = self._aliases.get(key)
        if node_id:
            return self._nodes.get(node_id)
        for node in self._nodes.values():
            if self.normalize_entity_name(node.name) == key:
                return node
        return None

    def disambiguate_entity(self, name: str, *, threshold: float = 0.5) -> TemporalEntityNode | None:
        """Resolve a near-match using token Jaccard similarity, without mutation."""
        direct = self.resolve_entity(name)
        if direct is not None:
            return direct
        query = set(self.normalize_entity_name(name).split())
        if not query:
            return None
        best: tuple[float, TemporalEntityNode | None] = (0.0, None)
        for node in self._nodes.values():
            tokens = set(self.normalize_entity_name(node.name).split())
            normalized = self.normalize_entity_name(node.name)
            score = max(
                len(query & tokens) / max(1, len(query | tokens)),
                SequenceMatcher(None, self.normalize_entity_name(name), normalized).ratio(),
            )
            if score > best[0]:
                best = (score, node)
        return best[1] if best[0] >= threshold else None

    def discover_communities(self, as_of: float | None = None) -> tuple[tuple[str, ...], ...]:
        """Return connected components of the active temporal graph."""
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in self._nodes}
        for edge in self.get_active_edges(as_of=as_of):
            adjacency.setdefault(edge.source_node_id, set()).add(edge.target_node_id)
            adjacency.setdefault(edge.target_node_id, set()).add(edge.source_node_id)
        communities: list[tuple[str, ...]] = []
        unseen = set(adjacency)
        while unseen:
            root = min(unseen)
            stack = [root]
            component: set[str] = set()
            while stack:
                node_id = stack.pop()
                if node_id not in unseen:
                    continue
                unseen.remove(node_id)
                component.add(node_id)
                stack.extend(adjacency.get(node_id, ()))
            communities.append(tuple(sorted(component)))
        return tuple(sorted(communities, key=lambda group: group[0] if group else ""))

    def hybrid_search(
        self,
        query: str,
        *,
        lexical_edges: Sequence[TemporalEdge] = (),
        vector_edges: Sequence[TemporalEdge] = (),
        top_k: int = 10,
        as_of: float | None = None,
    ) -> tuple[TemporalEdge, ...]:
        """Fuse lexical, vector and graph-active edge lanes with RRF ranks."""
        active = {edge.edge_id: edge for edge in self.get_active_edges(as_of=as_of)}
        query_terms = set(re.findall(r"\w+", str(query or "").lower()))
        lexical = list(lexical_edges) or [
            edge for edge in active.values()
            if query_terms & set(re.findall(r"\w+", edge.fact_statement.lower()))
        ]
        vector = list(vector_edges)
        graph = list(active.values())
        scores: dict[str, float] = {}
        for lane in (lexical, vector, graph):
            for rank, edge in enumerate(lane, 1):
                if edge.edge_id in active:
                    scores[edge.edge_id] = scores.get(edge.edge_id, 0.0) + 1.0 / (self.rrf_k + rank)
        ordered = sorted(scores, key=lambda edge_id: scores[edge_id], reverse=True)
        return tuple(active[edge_id] for edge_id in ordered[: max(1, top_k)])

    def extract_entities(self, text: str) -> tuple[TemporalEntityNode, ...]:
        """Extract conservative entity candidates for graph ingestion.

        This is intentionally deterministic and fail-soft: quoted spans,
        capitalized multi-word names, and CJK runs become candidates. It is a
        candidate generator, not an LLM assertion, so callers still need
        relation/evidence validation before persisting edges.
        """
        value = str(text or "")
        candidates: list[str] = []
        candidates.extend(re.findall(r"[\"']([^\"']{2,80})[\"']", value))
        candidates.extend(re.findall(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)+\b", value))
        candidates.extend(re.findall(r"[\u4e00-\u9fff]{2,12}", value))
        seen: set[str] = set()
        nodes: list[TemporalEntityNode] = []
        for candidate in candidates:
            key = self.normalize_entity_name(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            nodes.append(self.get_or_create_node(candidate))
        return tuple(nodes)

    async def extract_entities_with_llm(
        self,
        text: str,
        ai_call_fn: Callable[..., Awaitable[Any]],
    ) -> tuple[TemporalEntityNode, ...]:
        """Ask an LLM for entity candidates, falling back deterministically.

        The response is parsed as a JSON list of strings and bounded before
        node creation. This method never creates edges or asserts relations.
        """
        prompt = (
            "Extract only concrete entity names from the text. Return JSON "
            "array of strings, no explanations. Text: " + str(text or "")[:4000]
        )
        try:
            response = await ai_call_fn(
                "You are a conservative entity candidate extractor.",
                [{"role": "user", "content": prompt}],
            )
            raw = response.get("content", "") if isinstance(response, dict) else str(response)
            match = re.search(r"\[[^\]]*\]", raw, re.DOTALL)
            import json
            values = json.loads(match.group(0)) if match else []
            if isinstance(values, list):
                candidates = [str(value)[:120] for value in values if str(value).strip()][:32]
                if candidates:
                    return tuple(self.get_or_create_node(value) for value in dict.fromkeys(candidates))
        except Exception:
            pass
        return self.extract_entities(text)

    def get_or_create_node(self, name: str, entity_type: str = "concept") -> TemporalEntityNode:
        """Retrieve or register temporal entity node."""
        normalized = self.normalize_entity_name(name)
        alias_node_id = self._aliases.get(normalized)
        if alias_node_id is not None:
            return self._nodes[alias_node_id]
        node_id = f"node:{entity_type}:{normalized}"
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
