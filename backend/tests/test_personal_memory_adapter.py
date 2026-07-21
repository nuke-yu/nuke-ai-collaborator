import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.runtime import LegacyPersonalKnowledgeAdapter
from memory.contracts import CreatePersonalProjection, CreatePersonalRecord, MemoryOperationError
from memory.domain import MemoryScope
from memory.ports import PersonalKnowledgePort


class TestLegacyPersonalKnowledgeAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = LegacyPersonalKnowledgeAdapter()
        self.scope = MemoryScope.personal(user_id=7, actor_id="user:7")

    def test_adapter_implements_public_port(self):
        self.assertIsInstance(self.adapter, PersonalKnowledgePort)

    @patch("ai.personal_vault.add_record", new_callable=AsyncMock)
    async def test_explicit_record_authority_is_fixed_by_adapter(self, add):
        add.return_value = "personal:1"
        command = CreatePersonalRecord(scope=self.scope, kind="preference", content="Concise")
        self.assertEqual(await self.adapter.create_record(command), "personal:1")
        kwargs = add.await_args.kwargs
        self.assertEqual((kwargs["user_id"], kwargs["subject"]), (7, "7"))
        self.assertEqual((kwargs["authority"], kwargs["confidence"], kwargs["explicit"]),
                         ("user_statement", 1.0, True))

    @patch("ai.personal_vault.project", new_callable=AsyncMock)
    async def test_projection_preserves_explicit_group_and_bot_target(self, project):
        project.return_value = "projection:1"
        scope = MemoryScope.personal(user_id=7, actor_id="user:7", group_id=9)
        command = CreatePersonalProjection(scope=scope, record_id="personal:1",
                                           target_group_id=9, target_bot_id=5)
        self.assertEqual(await self.adapter.create_projection(command), "projection:1")
        self.assertEqual(project.await_args.kwargs["group_id"], 9)
        self.assertEqual(project.await_args.kwargs["bot_id"], 5)

    async def test_projection_cannot_widen_authorized_group(self):
        scope = MemoryScope.personal(user_id=7, actor_id="user:7", group_id=9)
        command = CreatePersonalProjection(scope=scope, record_id="personal:1", target_group_id=10)
        with self.assertRaisesRegex(MemoryOperationError, "authorized group"):
            await self.adapter.create_projection(command)

    @patch("ai.personal_vault.export_vault", new_callable=AsyncMock)
    async def test_export_uses_authenticated_personal_scope(self, export):
        export.return_value = {"user_id": 7, "records": []}
        self.assertEqual((await self.adapter.export(self.scope))["user_id"], 7)
        export.assert_awaited_once_with(7)

    @patch("ai.personal_vault.delete_vault", new_callable=AsyncMock)
    async def test_delete_uses_authenticated_personal_scope(self, delete):
        delete.return_value = True
        self.assertTrue(await self.adapter.delete(self.scope))
        delete.assert_awaited_once_with(7)

    async def test_group_scope_cannot_access_personal_vault(self):
        with self.assertRaisesRegex(MemoryOperationError, "personal scope"):
            await self.adapter.export(MemoryScope.group(group_id=1, actor_id="user:7"))


if __name__ == "__main__":
    unittest.main()
