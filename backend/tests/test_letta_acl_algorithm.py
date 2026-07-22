"""Unit tests for Task 11 (Letta Context Budget & OpenMemory ACL Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (LettaACLAlgorithmAdapter,
                                         LettaOpenMemoryEngine)
from memory.domain import MemoryScope, Principal


class TestLettaOpenMemoryEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = LettaOpenMemoryEngine()

    def test_principal_immutability_and_validation(self) -> None:
        p = Principal.user(10, [1, 2])
        self.assertIsInstance(p.group_ids, frozenset)
        self.assertIn(1, p.group_ids)
        with self.assertRaises(AttributeError):
            p.group_ids.add(999)  # type: ignore

        with self.assertRaises(ValueError):
            Principal(actor_id="", user_id=1)
        with self.assertRaises(ValueError):
            Principal(actor_id="bad", user_id=-5)
        with self.assertRaises(ValueError):
            Principal(actor_id="user:1", user_id=1, bot_id=2)
        with self.assertRaises(ValueError):
            # Counter-example 1: actor_id bot:999 with user_id 10 must be rejected
            Principal(actor_id="bot:999", user_id=10, group_ids=frozenset({7}))

    def test_check_acl_access_blocks_cross_group_bot_self_access(self) -> None:
        # Counter-example 2: Bot 5 in Group 1 attempting to access Bot 5 in Group 999 must be denied
        p = Principal.bot(bot_id=5, group_id=1)
        scope = MemoryScope.bot(group_id=999, bot_id=5, actor_id="bot:5")
        check = self.engine.check_acl_access(scope, principal=p, action="delete")
        self.assertFalse(check.allowed)
        self.assertIn("does not belong to target group 999", check.reason)

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
        principal = Principal.user(user_id=10, group_ids=[1])
        check = self.engine.check_acl_access(scope, principal=principal, action="read")
        self.assertTrue(check.allowed)

    def test_check_acl_access_blocks_cross_user_personal_access(self) -> None:
        scope = MemoryScope.personal(user_id=10, group_id=1, actor_id="user:10")
        principal = Principal.user(user_id=99, group_ids=[1])
        check = self.engine.check_acl_access(scope, principal=principal, action="read")
        self.assertFalse(check.allowed)
        self.assertIn("user 10", check.reason)

    def test_check_acl_access_blocks_cross_group_access(self) -> None:
        scope = MemoryScope.group(group_id=1, actor_id="user:99")
        # User belongs to group 2, attempting to access group 1
        principal = Principal.user(user_id=99, group_ids=[2])
        check = self.engine.check_acl_access(scope, principal=principal, action="read")
        self.assertFalse(check.allowed)
        self.assertIn("not a member", check.reason)

    def test_check_acl_access_enforces_bot_action_matrix_in_group(self) -> None:
        scope = MemoryScope.group(group_id=1, actor_id="bot:5")
        bot_principal = Principal.bot(bot_id=5, group_id=1)
        human_principal = Principal.user(user_id=10, group_ids=[1])

        # Bot reading group memory is allowed
        self.assertTrue(self.engine.check_acl_access(scope, principal=bot_principal, action="read").allowed)
        # Bot writing group memory directly is denied (requires HIL approval)
        self.assertFalse(self.engine.check_acl_access(scope, principal=bot_principal, action="write").allowed)
        # Human writing group memory is allowed
        self.assertTrue(self.engine.check_acl_access(scope, principal=human_principal, action="write").allowed)

    def test_check_acl_access_blocks_unsupported_action(self) -> None:
        scope = MemoryScope.personal(user_id=10, group_id=1, actor_id="user:10")
        principal = Principal.user(user_id=10, group_ids=[1])
        check = self.engine.check_acl_access(scope, principal=principal, action="unsupported_op")
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
        principal = Principal.bot(bot_id=2, group_id=1)
        check = await self.adapter.check_acl(scope, principal=principal, action="read")
        self.assertTrue(check.allowed)


if __name__ == "__main__":
    unittest.main()
