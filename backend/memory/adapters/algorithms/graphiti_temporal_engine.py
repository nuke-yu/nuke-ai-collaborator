"""Graphiti SQLite Temporal Graph & Invalidation Engine (Apache-2.0 ported algorithm).

Ported from Graphiti (Zep AI / Apache-2.0) Temporal Knowledge Graph:
- Bi-temporal Knowledge Graph nodes & edges with valid_at and invalid_at timestamps.
- Invalidate conflicting edges upon new state observation (set invalid_at = current_time) while preserving immutable history.
- Enable point-in-time active edge reconstruction as_of(timestamp).
"""
from __future__ import annotations

import time
import re
import uuid
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


@dataclass(frozen=True, slots=True)
class TemporalCommunity:
    """A materialized community view, including its internal graph edges."""

    community_id: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    density: float


@dataclass(frozen=True, slots=True)
class TemporalGraphPath:
    """One bounded path discovered by multi-hop traversal."""

    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]


class GraphitiTemporalEngine:
    """Audit-grade Graphiti Bi-Temporal Knowledge Graph & Invalidation Engine."""

    def __init__(self, *, rrf_k: int = 60) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        self._nodes: dict[str, TemporalEntityNode] = {}
        self._edges: list[TemporalEdge] = []
        self._aliases: dict[str, str] = {}
        self._token_index: dict[str, set[str]] = {}
        self._archived_edges: dict[str, TemporalEdge] = {}
        self.rrf_k = rrf_k
        self._functional_relations = frozenset({"has_status", "primary_role", "current_location", "lives_in", "preference"})

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
        for token in alias_key.split():
            self._token_index.setdefault(token, set()).add(node.node_id)
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
        candidate_ids: set[str] = set()
        for token in query:
            candidate_ids.update(self._token_index.get(token, ()))
        # Keep fuzzy matching bounded for large graphs.  If no indexed token
        # matches, inspect only a deterministic sample of recent node IDs.
        candidates = (candidate_ids or set(sorted(self._nodes)[-256:]))
        best: tuple[float, TemporalEntityNode | None] = (0.0, None)
        for node_id in candidates:
            node = self._nodes[node_id]
            tokens = set(self.normalize_entity_name(node.name).split())
            normalized = self.normalize_entity_name(node.name)
            score = max(
                len(query & tokens) / max(1, len(query | tokens)),
                SequenceMatcher(None, self.normalize_entity_name(name), normalized).ratio(),
            )
            if score > best[0]:
                best = (score, node)
        return best[1] if best[0] >= threshold else None

    def disambiguate_entities(
        self, name: str, *, threshold: float = 0.5, limit: int = 5
    ) -> tuple[TemporalEntityNode, ...]:
        """Return ranked, bounded candidates for large-scale entity linking."""
        if limit < 1:
            raise ValueError("limit must be positive")
        query = self.normalize_entity_name(name)
        query_tokens = set(query.split())
        ids: set[str] = set()
        for token in query_tokens:
            ids.update(self._token_index.get(token, ()))
        if not ids:
            ids = set(sorted(self._nodes)[-256:])
        ranked: list[tuple[float, TemporalEntityNode]] = []
        for node_id in ids:
            node = self._nodes[node_id]
            normalized = self.normalize_entity_name(node.name)
            tokens = set(normalized.split())
            score = max(
                len(query_tokens & tokens) / max(1, len(query_tokens | tokens)),
                SequenceMatcher(None, query, normalized).ratio(),
            )
            if score >= threshold:
                ranked.append((score, node))
        ranked.sort(key=lambda item: (-item[0], item[1].node_id))
        return tuple(node for _, node in ranked[:limit])

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

    def community_graph(self, as_of: float | None = None) -> tuple[TemporalCommunity, ...]:
        """Materialize connected communities with edge membership and density."""
        active = self.get_active_edges(as_of=as_of)
        components = self.discover_communities(as_of=as_of)
        result: list[TemporalCommunity] = []
        for nodes in components:
            node_set = set(nodes)
            edges = tuple(edge for edge in active
                          if edge.source_node_id in node_set and edge.target_node_id in node_set)
            possible = len(nodes) * (len(nodes) - 1)
            density = len(edges) / possible if possible else 0.0
            community_id = "community:" + uuid.uuid5(uuid.NAMESPACE_URL, ":".join(nodes)).hex
            result.append(TemporalCommunity(community_id, nodes,
                                            tuple(edge.edge_id for edge in edges), density))
        return tuple(result)

    def multi_hop_search(
        self, start_name: str, *, max_hops: int = 3, as_of: float | None = None,
        relation_types: Sequence[str] = (), max_paths: int = 100,
    ) -> tuple[TemporalGraphPath, ...]:
        """Perform bounded BFS with cycle protection and deterministic limits."""
        if max_hops < 1 or max_hops > 8:
            raise ValueError("max_hops must be between 1 and 8")
        if max_paths < 1:
            raise ValueError("max_paths must be positive")
        start = self.resolve_entity(start_name) or self.disambiguate_entity(start_name)
        if start is None:
            return ()
        allowed = set(relation_types)
        edges = self.get_active_edges(as_of=as_of)
        adjacency: dict[str, list[tuple[str, TemporalEdge]]] = {}
        for edge in edges:
            if allowed and edge.relation not in allowed:
                continue
            adjacency.setdefault(edge.source_node_id, []).append((edge.target_node_id, edge))
            adjacency.setdefault(edge.target_node_id, []).append((edge.source_node_id, edge))
        paths: list[TemporalGraphPath] = []
        frontier = [(start.node_id, (start.node_id,), ())]
        seen = {start.node_id}
        for _ in range(max_hops):
            next_frontier = []
            for node_id, node_path, edge_path in frontier:
                for neighbor, edge in sorted(adjacency.get(node_id, ()), key=lambda item: item[1].edge_id):
                    if edge.edge_id in edge_path or neighbor in seen:
                        continue
                    path = TemporalGraphPath(node_path + (neighbor,), edge_path + (edge.edge_id,))
                    paths.append(path)
                    if len(paths) >= max_paths:
                        return tuple(paths)
                    seen.add(neighbor)
                    next_frontier.append((neighbor, path.node_ids, path.edge_ids))
            frontier = next_frontier
            if not frontier:
                break
        return tuple(paths)

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
            for token in normalized.split():
                self._token_index.setdefault(token, set()).add(node_id)
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
        if relation in self._functional_relations:
            if any(
                edge.source_node_id == src_node.node_id
                and edge.relation == relation
                and edge.valid_at == now
                for edge in self._edges
            ):
                raise ValueError("functional relation already has an edge at valid_at")
            self.invalidate_conflicting_edges(
                source_node_id=src_node.node_id,
                relation=relation,
                invalid_at=now,
            )
            future_starts = [
                edge.valid_at for edge in self._edges
                if edge.source_node_id == src_node.node_id
                and edge.relation == relation
                and edge.valid_at > now
            ]
            invalid_at = min(future_starts, default=None)
        else:
            invalid_at = None

        edge_id = f"edge:{uuid.uuid4().hex}"
        edge = TemporalEdge(
            edge_id=edge_id,
            source_node_id=src_node.node_id,
            target_node_id=tgt_node.node_id,
            relation=relation,
            fact_statement=fact_statement,
            valid_at=now,
            invalid_at=invalid_at,
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
                and edge.valid_at < now
                and (edge.invalid_at is None or edge.invalid_at > now)
            ):
                edge.invalid_at = now
                invalidated.append(edge)

        return invalidated

    def get_active_edges(self, as_of: float | None = None) -> list[TemporalEdge]:
        """Query active temporal edges as of a specific point in time."""
        query_time = time.time() if as_of is None else as_of
        active: list[TemporalEdge] = []

        for edge in (*self._edges, *self._archived_edges.values()):
            if edge.valid_at <= query_time:
                if edge.invalid_at is None or edge.invalid_at > query_time:
                    active.append(edge)

        return active

    def archive_before(self, cutoff: float, *, limit: int = 1000) -> int:
        """Move invalid historical edges to cold storage without losing history."""
        if limit < 1:
            raise ValueError("limit must be positive")
        candidates = [edge for edge in self._edges
                      if edge.invalid_at is not None and edge.invalid_at <= cutoff]
        moved = 0
        for edge in sorted(candidates, key=lambda item: (item.invalid_at or 0, item.edge_id))[:limit]:
            self._archived_edges[edge.edge_id] = edge
            self._edges.remove(edge)
            moved += 1
        return moved

    def get_archived_edges(self) -> tuple[TemporalEdge, ...]:
        """Return cold historical edges for maintenance and audit tooling."""
        return tuple(sorted(self._archived_edges.values(), key=lambda edge: edge.edge_id))
