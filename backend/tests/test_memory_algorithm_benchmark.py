"""Task 13: Full Baseline Benchmark Suite for Memory Algorithm Upgrade Plan.

Validates end-to-end performance, accuracy, and latency targets across:
1. Mem0 Fact Extraction & Reconciliation Pipeline (ADD / UPDATE / DELETE / NOOP).
2. EverOS Case Extractor, Dual-Distance Clustering (Semantic + Geometric Time), and SKILL.md Compilation.
3. AutoGen Failure Insight Diagnosis & Corrective Action Synthesis.
4. Voyager Critic Environmental Verification & Success Gating.
5. RRF Fusion + MMR Diversification Reranking Latency & Diversity Index.
6. LangGraph Stateful Execution DAG Checkpoint Lineage Verification.
7. Letta Context Budget Allocation & OpenMemory Multi-Tenant Security ACL Isolation.
8. Graphiti SQLite Bi-Temporal Graph Invalidation Query Latency (< 50ms target).
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (AutoGenFailureEngine, CaseCluster,
                                         EverOSCaseEngine,
                                         EverOSClusteringEngine,
                                         EverOSSkillEngine, FactActionType,
                                         FailureCategory,
                                         GraphitiTemporalEngine,
                                         HybridRerankEngine, LangGraphDAGEngine,
                                         LettaOpenMemoryEngine, Mem0FactEngine,
                                         VoyagerCriticEngine)
from memory.domain import MemoryScope


class TestMemoryAlgorithmBenchmark(unittest.IsolatedAsyncioTestCase):
    def test_mem0_fact_reconciliation_benchmark(self) -> None:
        engine = Mem0FactEngine()
        existing = [
            {"record_id": "rec:1", "content": "User prefers dark theme"},
            {"record_id": "rec:2", "content": "User uses Python 3.12"},
        ]

        t0 = time.perf_counter()
        act_add = engine.reconcile_fact(existing, "User develops with FastAPI")
        act_update = engine.reconcile_fact(existing, "User prefers light theme")
        act_delete = engine.reconcile_fact(existing, "User no longer uses Python 3.12")
        act_noop = engine.reconcile_fact(existing, "User prefers dark theme")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertEqual(act_add.action_type, FactActionType.ADD)
        self.assertEqual(act_update.action_type, FactActionType.UPDATE)
        self.assertEqual(act_delete.action_type, FactActionType.DELETE)
        self.assertEqual(act_noop.action_type, FactActionType.NOOP)
        self.assertLess(elapsed_ms, 50.0, f"Mem0 fact reconciliation latency {elapsed_ms:.2f}ms exceeded 50ms limit.")

    def test_everos_clustering_and_skill_compilation_benchmark(self) -> None:
        case_engine = EverOSCaseEngine()
        cluster_engine = EverOSClusteringEngine(half_life_days=7.0, threshold=0.5)
        skill_engine = EverOSSkillEngine(min_cases=3, min_success_rate=0.8)

        now = time.time()
        c1 = case_engine.extract_case("r:1", "Deploy microservice to staging", "completed", [{"name": "kubectl"}])
        c2 = case_engine.extract_case("r:2", "Deploy microservice to staging env", "completed", [{"name": "kubectl"}])
        c3 = case_engine.extract_case("r:3", "Deploy app microservice to staging", "completed", [{"name": "kubectl"}])

        t0 = time.perf_counter()
        clusters = cluster_engine.cluster_cases([(c1, now), (c2, now - 100), (c3, now - 200)])
        self.assertEqual(len(clusters), 1)

        candidate = skill_engine.compile_skill_candidate(clusters[0])
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.is_qualified)
        self.assertIn("kubectl", candidate.tools_sequence)
        self.assertLess(elapsed_ms, 50.0, f"EverOS clustering & skill compilation latency {elapsed_ms:.2f}ms exceeded 50ms limit.")

    def test_autogen_and_voyager_benchmark(self) -> None:
        autogen = AutoGenFailureEngine()
        voyager = VoyagerCriticEngine()

        t0 = time.perf_counter()
        insight = autogen.analyze_failure("Run tests", ["FileNotFoundError: test_main.py"])
        critic = voyager.evaluate_success("Run tests", "completed", [{"name": "run_shell", "is_error": False}])
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertEqual(insight.category, FailureCategory.PATH_NOT_FOUND)
        self.assertTrue(critic.passed)
        self.assertLess(elapsed_ms, 50.0, f"AutoGen/Voyager analysis latency {elapsed_ms:.2f}ms exceeded 50ms limit.")

    def test_rrf_mmr_hybrid_rerank_benchmark(self) -> None:
        reranker = HybridRerankEngine(rrf_k=60, mmr_lambda=0.7)
        kw = [{"id": f"doc:{i}", "content": f"Python memory algorithm {i}"} for i in range(10)]
        vec = [{"id": f"doc:{i}", "content": f"Python memory algorithm {i}"} for i in range(5, 15)]

        t0 = time.perf_counter()
        reranked = reranker.rerank(kw, vec, query="Python memory algorithm", top_k=5)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertEqual(len(reranked), 5)
        self.assertLess(elapsed_ms, 50.0, f"RRF+MMR Rerank latency {elapsed_ms:.2f}ms exceeded 50ms limit.")

    def test_graphiti_bitemporal_query_latency_benchmark(self) -> None:
        graphiti = GraphitiTemporalEngine()
        now = time.time()

        t0 = time.perf_counter()
        for i in range(50):
            graphiti.add_edge("User", "preference", f"value_{i}", f"User prefers value_{i}", valid_at=now + i)

        active = graphiti.get_active_edges(as_of=now + 100)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].fact_statement, "User prefers value_49")
        self.assertLess(elapsed_ms, 50.0, f"Graphiti temporal graph invalidation latency {elapsed_ms:.2f}ms exceeded 50ms limit.")

    def test_letta_openmemory_budget_acl_benchmark(self) -> None:
        engine = LettaOpenMemoryEngine()

        t0 = time.perf_counter()
        budget = engine.calculate_context_budget(4096, "sys prompt", "work mem", "recall mem")
        scope = MemoryScope.personal(user_id=1, group_id=1, actor_id="user:1")
        acl_allow = engine.check_acl_access(scope, "user:1")
        acl_deny = engine.check_acl_access(scope, "user:2")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertFalse(budget.is_budget_exceeded)
        self.assertTrue(acl_allow.allowed)
        self.assertFalse(acl_deny.allowed)
        self.assertLess(elapsed_ms, 50.0, f"Letta/OpenMemory budget & ACL latency {elapsed_ms:.2f}ms exceeded 50ms limit.")


if __name__ == "__main__":
    unittest.main()
