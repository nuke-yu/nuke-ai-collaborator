"""Unit tests for Task 7 (EverOS Agent Skill Extractor Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (CaseCluster, EverOSCaseEngine,
                                         EverOSSkillAlgorithmAdapter,
                                         EverOSSkillEngine)


class TestEverOSSkillEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = EverOSCaseEngine()
        self.engine = EverOSSkillEngine(min_cases=3, min_success_rate=0.8)

    def test_compile_skill_candidate_qualifies_cluster_with_three_successful_cases(self) -> None:
        c1 = self.extractor.extract_case("run:1", "Deploy service to staging", "completed", [{"name": "kubectl"}, {"name": "helm"}])
        c2 = self.extractor.extract_case("run:2", "Deploy service to staging environment", "completed", [{"name": "kubectl"}, {"name": "helm"}])
        c3 = self.extractor.extract_case("run:3", "Deploy app service to staging", "completed", [{"name": "kubectl"}, {"name": "helm"}])

        cluster = CaseCluster(
            cluster_id="cluster:test",
            cases=(c1, c2, c3),
            centroid_signature=c1.task_signature,
            updated_at=time.time(),
        )

        candidate = self.engine.compile_skill_candidate(cluster)
        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.is_qualified)
        self.assertIn("kubectl", candidate.tools_sequence)
        self.assertIn("---", candidate.skill_md_content)
        self.assertIn("Required Tools", candidate.skill_md_content)

    def test_compile_skill_candidate_disqualifies_cluster_with_fewer_than_min_cases(self) -> None:
        c1 = self.extractor.extract_case("run:1", "Deploy service to staging", "completed", [{"name": "kubectl"}])

        cluster = CaseCluster(
            cluster_id="cluster:small",
            cases=(c1,),
            centroid_signature=c1.task_signature,
            updated_at=time.time(),
        )

        candidate = self.engine.compile_skill_candidate(cluster)
        self.assertIsNotNone(candidate)
        self.assertFalse(candidate.is_qualified)


class TestEverOSSkillAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = EverOSSkillAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.everos.skill_extractor")
        self.assertEqual(self.adapter.descriptor.source, "EverOS (everalgo)")
        self.assertEqual(self.adapter.descriptor.license, "Apache-2.0")

    async def test_adapter_compile_candidate_returns_candidate(self) -> None:
        extractor = EverOSCaseEngine()
        c1 = extractor.extract_case("run:1", "Optimize sqlite query", "completed", [{"name": "run_shell"}])
        cluster = CaseCluster(
            cluster_id="cluster:adapter",
            cases=(c1,),
            centroid_signature=c1.task_signature,
            updated_at=time.time(),
        )

        candidate = await self.adapter.compile_candidate(cluster)
        self.assertIsNotNone(candidate)
        self.assertIn("skill:", candidate.skill_id)


if __name__ == "__main__":
    unittest.main()
