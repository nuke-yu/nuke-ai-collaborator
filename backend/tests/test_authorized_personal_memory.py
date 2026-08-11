from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import ACLPermissionCheck
from memory.application import AuthorizedPersonalKnowledgeService
from memory.contracts import (
    CreatePersonalProjection,
    CreatePersonalRecord,
    FormatProjectedContext,
    MemoryAuthorizationError,
)
from memory.domain import MemoryScope, Principal


class TestAuthorizedPersonalKnowledgeService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.delegate = AsyncMock()
        self.acl = AsyncMock()
        self.acl.check_acl.return_value = ACLPermissionCheck(True, "allowed")
        self.principal = Principal.user(10, [7])
        self.service = AuthorizedPersonalKnowledgeService(
            self.delegate, self.acl, self.principal
        )
        self.scope = MemoryScope.personal(
            user_id=10, actor_id="user:10", purpose="test"
        )

    async def test_write_is_authorized_before_delegate(self) -> None:
        self.delegate.create_record.return_value = "record:1"

        result = await self.service.create_record(
            CreatePersonalRecord(scope=self.scope, kind="preference", content="concise")
        )

        self.assertEqual(result, "record:1")
        self.assertEqual(self.acl.check_acl.await_args.kwargs["action"], "write")
        self.assertIs(self.acl.check_acl.await_args.kwargs["principal"], self.principal)
        self.delegate.create_record.assert_awaited_once()

    async def test_denial_never_reaches_delegate(self) -> None:
        self.acl.check_acl.return_value = ACLPermissionCheck(False, "denied")

        with self.assertRaisesRegex(MemoryAuthorizationError, "denied"):
            await self.service.create_record(
                CreatePersonalRecord(scope=self.scope, kind="preference", content="concise")
            )

        self.delegate.create_record.assert_not_awaited()

    @patch(
        "ai.personal_vault.evaluate_access_control_rule",
        new_callable=AsyncMock,
        return_value=False,
    )
    async def test_explicit_abac_deny_tightens_default_acl(self, evaluate_rule) -> None:
        with self.assertRaisesRegex(MemoryAuthorizationError, "ABAC deny"):
            await self.service.create_record(
                CreatePersonalRecord(scope=self.scope, kind="preference", content="concise")
            )
        evaluate_rule.assert_awaited_once()
        self.delegate.create_record.assert_not_awaited()

    async def test_scope_cannot_impersonate_another_actor(self) -> None:
        forged = MemoryScope.personal(
            user_id=10, actor_id="user:99", purpose="forged"
        )

        with self.assertRaisesRegex(MemoryAuthorizationError, "does not match"):
            await self.service.export(forged)

        self.acl.check_acl.assert_not_awaited()
        self.delegate.export.assert_not_awaited()

    async def test_projection_authorizes_source_and_target(self) -> None:
        self.delegate.create_projection.return_value = "projection:1"
        command = CreatePersonalProjection(
            scope=MemoryScope.personal(
                user_id=10, group_id=7, actor_id="user:10", purpose="project"
            ),
            record_id="record:1",
            target_group_id=7,
        )

        result = await self.service.create_projection(command)

        self.assertEqual(result, "projection:1")
        self.assertEqual(self.acl.check_acl.await_count, 2)
        source, target = self.acl.check_acl.await_args_list
        self.assertEqual(source.kwargs["action"], "project")
        self.assertEqual(target.args[0].group_id, 7)
        self.assertEqual(target.kwargs["action"], "project")

    async def test_projected_context_requires_personal_and_group_read(self) -> None:
        self.delegate.format_projected_context.return_value = "context"
        command = FormatProjectedContext(
            scope=MemoryScope.personal(
                user_id=10, group_id=7, actor_id="user:10", purpose="read"
            )
        )

        result = await self.service.format_projected_context(command)

        self.assertEqual(result, "context")
        self.assertEqual(
            [call.kwargs["action"] for call in self.acl.check_acl.await_args_list],
            ["read", "read"],
        )


if __name__ == "__main__":
    unittest.main()
