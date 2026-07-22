"""Unit tests for Task 3 (Mem0 Fact Extraction & Reconciliation Algorithm)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (FactActionType, Mem0FactAlgorithmAdapter,
                                         Mem0FactEngine)
from memory.contracts import ObserveMemory
from memory.domain import MemoryScope


class TestMem0FactEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = Mem0FactEngine()

    def test_extract_candidate_facts_splits_sentences_and_filters_chatter(self) -> None:
        text = "Hello! User prefers dark mode. Thank you. User works with Python and SQLite."
        facts = self.engine.extract_candidate_facts(text)
        self.assertIn("User prefers dark mode", facts)
        self.assertIn("User works with Python and SQLite", facts)
        self.assertNotIn("Hello", facts)
        self.assertNotIn("Thank you", facts)

    def test_reconcile_fact_returns_add_for_new_topic(self) -> None:
        existing = []
        action = self.engine.reconcile_fact(existing, "User uses macOS for development")
        self.assertEqual(action.action_type, FactActionType.ADD)
        self.assertEqual(action.content, "User uses macOS for development")

    def test_reconcile_fact_returns_noop_for_exact_or_near_duplicate(self) -> None:
        existing = [{"record_id": "rec:1", "content": "User prefers dark mode"}]
        action = self.engine.reconcile_fact(existing, "User prefers dark mode")
        self.assertEqual(action.action_type, FactActionType.NOOP)
        self.assertEqual(action.target_record_id, "rec:1")

    def test_reconcile_fact_returns_update_for_attribute_value_change(self) -> None:
        existing = [{"record_id": "rec:2", "content": "User preferred theme is light"}]
        action = self.engine.reconcile_fact(existing, "User preferred theme is dark")
        self.assertEqual(action.action_type, FactActionType.UPDATE)
        self.assertEqual(action.target_record_id, "rec:2")
        self.assertEqual(action.old_content, "User preferred theme is light")

    def test_reconcile_fact_returns_delete_for_explicit_refutation(self) -> None:
        existing = [{"record_id": "rec:3", "content": "User uses Windows 11"}]
        action = self.engine.reconcile_fact(existing, "User no longer uses Windows 11")
        self.assertEqual(action.action_type, FactActionType.DELETE)
    async def test_reconcile_with_llm_parses_json_actions(self) -> None:
        mock_ai_call = AsyncMock(return_value={
            "content": '[{"action": "ADD", "fact": "User speaks French", "target_record_id": null, "reason": "new language"}]'
        })
        actions = await self.engine.reconcile_with_llm(
            "I speak French fluently.", [], ai_call_fn=mock_ai_call
        )
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, FactActionType.ADD)
        self.assertEqual(actions[0].content, "User speaks French")


class TestMem0FactAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = Mem0FactAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.mem0.fact_pipeline")
        self.assertEqual(self.adapter.descriptor.source, "mem0 (mem0ai)")
        self.assertEqual(self.adapter.descriptor.license, "Apache-2.0")

    async def test_adapter_extract_returns_structured_actions(self) -> None:
        scope = MemoryScope.bot(group_id=9, bot_id=5, actor_id="bot:5")
        command = ObserveMemory(
            scope=scope,
            source_id="m:100",
            content="User prefers tabs for indentation. User no longer uses spaces.",
            metadata={"message_id": 100},
        )
        existing = [{"record_id": "rec:spaces", "content": "User uses spaces for indentation"}]

        actions = await self.adapter.extract(command, existing)
        self.assertGreaterEqual(len(actions), 2)
        action_types = [a["action"] for a in actions]
        self.assertIn("ADD", action_types)
        self.assertIn("DELETE", action_types)


if __name__ == "__main__":
    unittest.main()
