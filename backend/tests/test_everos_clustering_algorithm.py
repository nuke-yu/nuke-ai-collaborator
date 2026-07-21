"""Unit tests for Task 6 (EverOS Case Clustering Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (EverOSCaseAlgorithmAdapter,
                                         EverOSCaseEngine,
                                         EverOSClusteringAlgorithmAdapter,
                                         EverOSClusteringEngine)


class TestEverOSClusteringEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = EverOSCaseEngine()
        self.engine = EverOSClusteringEngine(half_life_days=7.0, threshold=0.5)

    def test_semantic_similarity_high_for_similar_tasks(self) -> None:
        sim = self.engine.calculate_semantic_similarity(
            "Fix syntax error in main module",
            "Fix syntax error in main file",
            tools_a=["read_file", "run_shell"],
            tools_b=["read_file", "run_shell"],
        )
        self.assertGreater(sim, 0.7)

    def test_semantic_similarity_low_for_distinct_tasks(self) -> None:
        sim = self.engine.calculate_semantic_similarity(
            "Fix syntax error in main module",
            "Deploy docker container to production k8s",
            tools_a=["read_file"],
            tools_b=["kubectl", "docker"],
        )
        self.assertLess(sim, 0.3)

    def test_geometric_time_decay_halves_at_seven_days(self) -> None:
        now = time.time()
        seven_days_ago = now - (7 * 86400)
        decay = self.engine.calculate_time_decay(now, seven_days_ago)
        self.assertAlmostEqual(decay, 0.5, delta=0.01)

    def test_cluster_cases_groups_recent_similar_cases(self) -> None:
        now = time.time()
        c1 = self.extractor.extract_case("run:1", "Fix database timeout in postgres", "completed", [{"name": "db_query"}])
        c2 = self.extractor.extract_case("run:2", "Fix database timeout error in postgres", "completed", [{"name": "db_query"}])
        c3 = self.extractor.extract_case("run:3", "Build frontend UI header component", "completed", [{"name": "edit_file"}])

        clusters = self.engine.cluster_cases([
            (c1, now),
            (c2, now - 3600),
            (c3, now - 7200),
        ])

        self.assertEqual(len(clusters), 2)
        # Cluster 1 contains c1 and c2
        self.assertEqual(len(clusters[0].cases), 2)
        # Cluster 2 contains c3
        self.assertEqual(len(clusters[1].cases), 1)


class TestEverOSClusteringAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = EverOSClusteringAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.everos.case_clustering")
        self.assertEqual(self.adapter.descriptor.source, "EverOS (everalgo)")
        self.assertEqual(self.adapter.descriptor.license, "Apache-2.0")

    async def test_adapter_cluster_returns_clusters(self) -> None:
        extractor = EverOSCaseEngine()
        case = extractor.extract_case("run:10", "Refactor user authentication", "completed", [])
        clusters = await self.adapter.cluster([(case, time.time())])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].cases[0].case_id, "case:run:10")


if __name__ == "__main__":
    unittest.main()
