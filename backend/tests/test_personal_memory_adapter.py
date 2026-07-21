import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.runtime import LegacyPersonalKnowledgeAdapter
from memory.contracts import MemoryOperationError
from memory.domain import MemoryScope
from memory.ports import PersonalKnowledgePort


class TestLegacyPersonalKnowledgeAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = LegacyPersonalKnowledgeAdapter()
        self.scope = MemoryScope.personal(user_id=7, actor_id="user:7")

    def test_adapter_implements_public_port(self):
        self.assertIsInstance(self.adapter, PersonalKnowledgePort)

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
