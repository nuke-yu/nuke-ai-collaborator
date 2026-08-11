"""Unit tests for Task 5 (AutoGen Failure Insight Learning Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (AutoGenFailureAlgorithmAdapter,
                                         AutoGenFailureEngine, FailureCategory)


class TestAutoGenFailureEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = AutoGenFailureEngine()

    def test_analyze_failure_diagnoses_path_not_found(self) -> None:
        errors = ["FileNotFoundError: [Errno 2] No such file or directory: 'config/missing.json'"]
        insight = self.engine.analyze_failure("Read config file", errors)
        self.assertEqual(insight.category, FailureCategory.PATH_NOT_FOUND)
        self.assertIn("list_dir", insight.corrective_action)

    def test_analyze_failure_diagnoses_syntax_error(self) -> None:
        errors = ["SyntaxError: invalid syntax (line 42)"]
        insight = self.engine.analyze_failure("Compile Python code", errors)
        self.assertEqual(insight.category, FailureCategory.SYNTAX_ERROR)
        self.assertIn("syntax", insight.corrective_action)

    def test_analyze_failure_diagnoses_permission_denied(self) -> None:
        errors = ["PermissionError: [Errno 13] Permission denied: '/etc/passwd'"]
        insight = self.engine.analyze_failure("Modify file", errors)
        self.assertEqual(insight.category, FailureCategory.PERMISSION_DENIED)
        self.assertIn("workspace", insight.corrective_action)

    def test_analyze_failure_diagnoses_timeout(self) -> None:
        errors = ["TimeoutError: Command 'pytest' timed out after 60 seconds"]
        insight = self.engine.analyze_failure("Run tests", errors)
        self.assertEqual(insight.category, FailureCategory.TIMEOUT)
        self.assertIn("time limit", insight.insight_summary)

    def test_analyze_failure_diagnoses_invalid_argument(self) -> None:
        tool_records = [
            {"name": "write_file", "result": "TypeError: missing required argument 'TargetFile'", "is_error": True}
        ]
        insight = self.engine.analyze_failure("Write file", [], tool_records)
        self.assertEqual(insight.category, FailureCategory.INVALID_ARGUMENT)
        self.assertIn("argument", insight.corrective_action)
    async def test_analyze_failure_with_llm_parses_json_insight(self) -> None:
        from unittest.mock import AsyncMock
        mock_ai_call = AsyncMock(return_value={
            "content": '{"category": "path_not_found", "insight_summary": "Missing config file", "corrective_action": "Check directory", "relevancy_score": 0.98}'
        })
        insight = await self.engine.analyze_failure_with_llm(
            "Read config", ["FileNotFoundError: missing.json"], ai_call_fn=mock_ai_call
        )
        self.assertEqual(insight.category, FailureCategory.PATH_NOT_FOUND)
        self.assertEqual(insight.insight_summary, "Missing config file")
        self.assertEqual(insight.relevancy_score, 0.98)

    async def test_run_with_retry_injects_insight_until_validation_succeeds(self) -> None:
        attempts = []

        async def attempt(task, insights):
            attempts.append((task, insights))
            return "fixed" if insights else "FileNotFoundError: missing.py"

        async def validate(response):
            return response == "fixed"

        result = await self.engine.run_with_retry(
            "read config", attempt, validate, max_retries=2
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(result.insights), 1)
        self.assertEqual(len(attempts[1][1]), 1)

    async def test_run_with_retry_respects_retry_budget(self) -> None:
        calls = 0

        async def attempt(_task, _insights):
            nonlocal calls
            calls += 1
            return "still failing"

        result = await self.engine.run_with_retry(
            "task", attempt, lambda _response: _always_false(), max_retries=1
        )
        self.assertFalse(result.succeeded)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(calls, 2)


async def _always_false() -> bool:
    return False


class TestAutoGenFailureAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = AutoGenFailureAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.autogen.failure_insight")
        self.assertEqual(self.adapter.descriptor.source, "AutoGen (MIT)")
        self.assertEqual(self.adapter.descriptor.license, "MIT")

    async def test_adapter_analyze_returns_insight(self) -> None:
        insight = await self.adapter.analyze("Run script", ["FileNotFoundError: missing.py"])
        self.assertEqual(insight.category, FailureCategory.PATH_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
