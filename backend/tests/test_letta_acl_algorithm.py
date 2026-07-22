"""Unit tests for Task 11 (Letta Context Budget & OpenMemory ACL Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (LettaACLAlgorithmAdapter,
                                         LettaOpenMemoryEngine)
from memory.domain import MemoryScope


class TestLettaOpenMemoryEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = LettaOpenMemoryEngine()

    def test_calculate_context_budget_computes_allocations(self) -> None:
        budget = self.engine.calculate_context_budget(
            max_tokens=4096,
            system_prompt="System prompt content",
            working_memory="Working memory context",
            recall_memory="Recall memory content",
            tool_schemas=[{"name": "read_file"}],
        )

        self.assertEqual(budget.max_tokens, 4096)
        self.assertGreater(budget.available_for_generation, 0)
        self.assertFalse(budget.is_budget_exceeded)

    def test_calculate_context_budget_flags_exceeded_budget(self) -> None:
        huge_text = "a" * 16000
        budget = self.engine.calculate_context_budget(
            max_tokens=2000,
            system_prompt=huge_text,
            working_memory="",
            recall_memory="",
        )

        self.assertTrue(budget.is_budget_exceeded)

    def test_check_acl_access_grants_personal_owner(self) -> None:
        scope = MemoryScope.personal(user_id=10, group_id=1, actor_id="user:10")
        check = self.engine.check_acl_access(scope, requesting_actor_id="user:10")
        self.assertTrue(check.allowed)

    def test_check_acl_access_blocks_cross_user_personal_access(self) -> None:
        scope = MemoryScope.personal(user_id=10, group_id=1, actor_id="user:10")
        check = self.engine.check_acl_access(scope, requesting_actor_id="user:99")
        self.assertFalse(check.allowed)
        self.assertIn("user 10", check.reason)


    def test_check_acl_access_blocks_cross_group_access(self) -> None:
        scope = MemoryScope.group(group_id=1, actor_id="user:99")
        # User belongs to group 2, but attempting to access group 1
        check = self.engine.check_acl_access(scope, requesting_actor_id="user:99", actor_group_ids=[2])
        self.assertFalse(check.allowed)
        self.assertIn("not a member", check.reason)

    def test_check_acl_access_blocks_unsupported_action(self) -> None:
        scope = MemoryScope.personal(user_id=10, group_id=1, actor_id="user:10")
        check = self.engine.check_acl_access(scope, requesting_actor_id="user:10", action="unsupported_op")
        self.assertFalse(check.allowed)
        self.assertIn("Unsupported action", check.reason)


class TestLettaACLAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = LettaACLAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.letta_openmemory.budget_acl")
        self.assertIn("Letta", self.adapter.descriptor.source)
        self.assertEqual(self.adapter.descriptor.license, "Apache-2.0")

    async def test_adapter_calculate_budget_and_acl_check(self) -> None:
        budget = await self.adapter.calculate_budget(4096, "sys", "work", "rec")
        self.assertEqual(budget.max_tokens, 4096)

        scope = MemoryScope.bot(group_id=1, bot_id=2, actor_id="bot:2")
        check = await self.adapter.check_acl(scope, "bot:2", actor_group_ids=[1])
        self.assertTrue(check.allowed)


if __name__ == "__main__":
    unittest.main()
