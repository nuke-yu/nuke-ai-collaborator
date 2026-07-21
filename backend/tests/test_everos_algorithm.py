"""Unit tests for Task 4 (EverOS Agent Case Extractor Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (EverOSCaseAlgorithmAdapter,
                                         EverOSCaseEngine)
from memory.contracts import AssembleCase
from memory.domain import MemoryScope


class TestEverOSCaseEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = EverOSCaseEngine()

    def test_extract_case_builds_structured_extracted_case(self) -> None:
        tool_records = [
            {"name": "read_file", "args": {"path": "src/main.py"}, "result": "ok", "is_error": False},
            {"name": "run_shell", "args": {"command": "pytest"}, "result": "SyntaxError", "is_error": True},
        ]

        extracted = self.engine.extract_case(
            run_id="run:101",
            task="Fix syntax error in main.py",
            outcome="completed",
            tool_records=tool_records,
        )

        self.assertEqual(extracted.case_id, "case:run:101")
        self.assertIn("read_file", extracted.tools_used)
        self.assertIn("run_shell", extracted.tools_used)
        self.assertIn("src/main.py", extracted.files_touched)
        self.assertEqual(len(extracted.errors), 1)
        self.assertTrue(extracted.should_distill)
        self.assertEqual(extracted.information_gain, "high")
        self.assertIn("corrected_success", extracted.verification_signals)

    def test_evaluate_outcome_ordinary_success_does_not_distill(self) -> None:
        tool_records = [
            {"name": "read_file", "args": {"path": "README.md"}, "result": "content", "is_error": False}
        ]

        extracted = self.engine.extract_case(
            run_id="run:102",
            task="Read readme file",
            outcome="completed",
            tool_records=tool_records,
        )

        self.assertFalse(extracted.should_distill)
        self.assertEqual(extracted.information_gain, "low")

    def test_task_signature_is_deterministic_and_normalized(self) -> None:
        sig1 = self.engine.task_signature("  Fix  Bug IN   main.py ")
        sig2 = self.engine.task_signature("fix bug in main.py")
        self.assertEqual(sig1, sig2)


class TestEverOSCaseAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = EverOSCaseAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.everos.case_extractor")
        self.assertEqual(self.adapter.descriptor.source, "EverOS (everalgo)")
        self.assertEqual(self.adapter.descriptor.license, "Apache-2.0")

    async def test_adapter_extract_case_returns_extracted_case(self) -> None:
        scope = MemoryScope.bot(group_id=9, bot_id=5, actor_id="bot:5")
        command = AssembleCase(
            scope=scope,
            run_id="run:55",
            task="Debug database timeout",
            outcome="completed",
            tool_records=({"name": "run_shell", "args": {"command": "pytest"}, "result": "Timeout", "is_error": True},),
        )

        extracted = await self.adapter.extract_case(command)
        self.assertEqual(extracted.case_id, "case:run:55")
        self.assertTrue(extracted.should_distill)


if __name__ == "__main__":
    unittest.main()
