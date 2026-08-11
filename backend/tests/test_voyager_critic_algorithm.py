"""Unit tests for Task 8 (Voyager Critic Success Gate Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (VoyagerCriticAlgorithmAdapter,
                                         VoyagerCriticEngine)


class TestVoyagerCriticEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = VoyagerCriticEngine()

    def test_evaluate_success_clean_run_passes(self) -> None:
        result = self.engine.evaluate_success("Build app", "completed", [{"name": "run_shell", "is_error": False}])
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 1.0)

    def test_evaluate_success_failed_outcome_fails(self) -> None:
        result = self.engine.evaluate_success("Build app", "failed", [])
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 0.0)

    def test_evaluate_success_recovered_run_passes(self) -> None:
        records = [
            {"name": "run_shell", "is_error": True},
            {"name": "run_shell", "is_error": False},
        ]
        result = self.engine.evaluate_success("Fix test", "completed", records)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 0.85)

    def test_build_curriculum_orders_dependencies_and_difficulty(self) -> None:
        ordered = self.engine.build_curriculum([
            {"id": "deploy", "depends_on": ["test"], "difficulty": 3},
            {"id": "test", "depends_on": ["build"], "difficulty": 2},
            {"id": "build", "difficulty": 1},
        ])
        self.assertEqual([item["id"] for item in ordered], ["build", "test", "deploy"])

    def test_build_curriculum_rejects_cycles(self) -> None:
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.engine.build_curriculum([
                {"id": "a", "depends_on": ["b"]},
                {"id": "b", "depends_on": ["a"]},
            ])

    async def test_evaluate_success_with_llm_parses_json_critique(self) -> None:
        mock_ai_call = AsyncMock(return_value={
            "content": '{"passed": true, "score": 0.98, "critique": "All verification assertions satisfied"}'
        })
        result = await self.engine.evaluate_success_with_llm(
            "Verify build", "completed", [], ai_call_fn=mock_ai_call
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 0.98)
        self.assertEqual(result.verification_mode, "llm_reflection")


class TestVoyagerCriticAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = VoyagerCriticAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.voyager.critic_gate")
        self.assertEqual(self.adapter.descriptor.source, "Voyager (GPL-3.0 / MIT)")
        self.assertEqual(self.adapter.descriptor.license, "MIT")

    async def test_adapter_evaluate_returns_result(self) -> None:
        result = await self.adapter.evaluate("Deploy app", "completed", [])
        self.assertTrue(result.passed)


if __name__ == "__main__":
    unittest.main()
