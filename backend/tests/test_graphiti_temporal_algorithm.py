"""Unit tests for Task 12 (Graphiti SQLite Temporal Graph & Invalidation Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import time
import asyncio
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (GraphitiTemporalAlgorithmAdapter,
                                         GraphitiTemporalEngine)


class TestGraphitiTemporalEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = GraphitiTemporalEngine()

    def test_add_edge_registers_nodes_and_active_edge(self) -> None:
        t0 = time.time()
        edge = self.engine.add_edge("User", "lives_in", "Seattle", "User lives in Seattle", valid_at=t0)
        self.assertEqual(edge.relation, "lives_in")
        self.assertIsNone(edge.invalid_at)

        active = self.engine.get_active_edges(as_of=t0 + 10)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].edge_id, edge.edge_id)

    def test_add_conflicting_edge_invalidates_prior_edge_with_timestamp(self) -> None:
        t0 = 1000.0
        t1 = 2000.0

        e1 = self.engine.add_edge("User", "lives_in", "Seattle", "User lives in Seattle", valid_at=t0)
        self.assertIsNone(e1.invalid_at)

        e2 = self.engine.add_edge("User", "lives_in", "San Francisco", "User lives in San Francisco", valid_at=t1)
        self.assertEqual(e1.invalid_at, t1)
        self.assertIsNone(e2.invalid_at)

        # Historical query at t0+500 shows e1 active
        active_t0 = self.engine.get_active_edges(as_of=t0 + 500)
        self.assertEqual(len(active_t0), 1)
        self.assertEqual(active_t0[0].edge_id, e1.edge_id)

        # Current query at t1+500 shows e2 active
        active_t1 = self.engine.get_active_edges(as_of=t1 + 500)
        self.assertEqual(len(active_t1), 1)
        self.assertEqual(active_t1[0].edge_id, e2.edge_id)

    def test_entity_aliases_resolve_to_one_node(self) -> None:
        canonical = self.engine.register_alias("  SF  ", "San Francisco")
        edge = self.engine.add_edge(
            "User", "lives_in", "SF", "User lives in SF", valid_at=1000.0
        )
        self.assertEqual(edge.target_node_id, canonical.node_id)
        self.assertEqual(self.engine.normalize_entity_name(" San   Francisco! "), "san francisco")

    def test_entity_resolution_and_conservative_extraction(self) -> None:
        canonical = self.engine.register_alias("SF", "San Francisco")
        self.assertEqual(self.engine.resolve_entity(" sf ").node_id, canonical.node_id)
        entities = self.engine.extract_entities(
            'User moved to "San Francisco" while working with Open AI'
        )
        names = {self.engine.normalize_entity_name(node.name) for node in entities}
        self.assertIn("san francisco", names)
        self.assertIn("open ai", names)

    def test_disambiguation_and_community_discovery(self) -> None:
        self.engine.add_edge("Alice Smith", "works_with", "Bob Jones", "team", valid_at=1)
        self.engine.add_edge("Carol", "knows", "Dan", "friends", valid_at=1)
        self.assertEqual(
            self.engine.disambiguate_entity("Alice Smit").name,
            "Alice Smith",
        )
        communities = self.engine.discover_communities(as_of=2)
        self.assertEqual(len(communities), 2)

    def test_hybrid_search_fuses_active_graph_and_rank_lanes(self) -> None:
        first = self.engine.add_edge("A", "knows", "B", "deployment path", valid_at=1)
        second = self.engine.add_edge("B", "uses", "C", "database path", valid_at=1)
        results = self.engine.hybrid_search(
            "deployment", lexical_edges=[first], vector_edges=[second], top_k=2, as_of=2
        )
        self.assertEqual({edge.edge_id for edge in results}, {first.edge_id, second.edge_id})

    def test_llm_entity_candidates_fallback_and_parse(self) -> None:
        async def ai_call(_system, _messages):
            return {"content": '["Nuke", "SQLite"]'}

        entities = asyncio.run(
            self.engine.extract_entities_with_llm("database note", ai_call)
        )
        self.assertEqual({node.name for node in entities}, {"Nuke", "SQLite"})


class TestGraphitiTemporalAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = GraphitiTemporalAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.graphiti.temporal_graph")
        self.assertEqual(self.adapter.descriptor.source, "Graphiti (Zep AI / Apache-2.0)")
        self.assertEqual(self.adapter.descriptor.license, "Apache-2.0")

    async def test_adapter_add_and_get_active_facts(self) -> None:
        edge = await self.adapter.add_temporal_fact("User", "works_at", "Google", "User works at Google")
        self.assertEqual(edge.relation, "works_at")

        active = await self.adapter.get_active_facts()
        self.assertEqual(len(active), 1)

    async def test_adapter_exposes_entity_and_community_capabilities(self) -> None:
        await self.adapter.add_temporal_fact("A", "knows", "B", "A knows B", valid_at=1)
        candidates = await self.adapter.extract_entity_candidates('"Nuke"')
        self.assertEqual(candidates[0].name, "Nuke")
        self.assertEqual(len(await self.adapter.discover_communities(as_of=2)), 2)


if __name__ == "__main__":
    unittest.main()
