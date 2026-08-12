"""Unit tests for Task 9 (RRF + MMR Hybrid Search & Reranker Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (HybridRerankAlgorithmAdapter,
                                         HybridRerankEngine)


class TestHybridRerankEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HybridRerankEngine(rrf_k=60, mmr_lambda=0.7)

    def test_rrf_fusion_calculates_correct_reciprocal_rank_scores(self) -> None:
        list_a = [{"id": "doc1", "content": "Python backend"}, {"id": "doc2", "content": "React frontend"}]
        list_b = [{"id": "doc2", "content": "React frontend"}, {"id": "doc3", "content": "SQLite database"}]

        fused = self.engine.rrf_fusion([list_a, list_b])
        self.assertEqual(len(fused), 3)

        # doc2 appears in rank 2 of list_a (1/62) and rank 1 of list_b (1/61)
        doc2 = next(item for item in fused if item["id"] == "doc2")
        expected_score = (1.0 / 62) + (1.0 / 61)
        self.assertAlmostEqual(doc2["rrf_score"], expected_score, places=5)

    def test_mmr_diversify_penalizes_redundant_results(self) -> None:
        candidates = [
            {"id": "doc1", "content": "Python FastAPI backend server", "rrf_score": 0.03},
            {"id": "doc2", "content": "Python FastAPI backend server duplicate", "rrf_score": 0.029},
            {"id": "doc3", "content": "React Tailwind UI component", "rrf_score": 0.025},
        ]

        diversified = self.engine.mmr_diversify(candidates, query="Python backend component", top_k=2)
        self.assertEqual(len(diversified), 2)
        selected_ids = [d["id"] for d in diversified]
        self.assertIn("doc1", selected_ids)
        self.assertIn("doc3", selected_ids)
        self.assertNotIn("doc2", selected_ids)

    def test_rerank_full_pipeline(self) -> None:
        kw = [{"id": "1", "content": "sqlite database connection"}]
        vec = [{"id": "2", "content": "postgres sql database"}]

        results = self.engine.rerank(kw, vec, query="database connection", top_k=2)
        self.assertEqual(len(results), 2)

    def test_rerank_accepts_graph_rank_lane(self) -> None:
        results = self.engine.rerank(
            [{"id": "a", "content": "alpha"}],
            [],
            query="alpha",
            top_k=2,
            graph_hits=[{"id": "b", "content": "related alpha"}],
        )
        self.assertEqual({item["id"] for item in results}, {"a", "b"})

    def test_calibration_and_optional_reranker_are_fail_soft(self) -> None:
        calibrated = self.engine.calibrate_scores([{"id": "a", "score": 10}, {"id": "b", "score": 20}])
        self.assertEqual(calibrated[0]["score_calibrated"], 0.0)
        results = self.engine.rerank(
            [{"id": "a", "content": "alpha"}], [], query="alpha",
            reranker=lambda _query, items: list(reversed(items)),
        )
        self.assertEqual(results[0]["id"], "a")


class TestHybridRerankAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = HybridRerankAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.hybrid.rrf_mmr_rerank")
        self.assertIn("RRF", self.adapter.descriptor.source)
        self.assertEqual(self.adapter.descriptor.license, "Apache-2.0")

    async def test_adapter_rerank_returns_results(self) -> None:
        results = await self.adapter.rerank(
            [{"id": "a", "content": "test item"}],
            [{"id": "b", "content": "test item 2"}],
            query="test",
            top_k=1,
        )
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
