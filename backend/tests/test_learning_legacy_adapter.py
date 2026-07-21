import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.runtime import LegacyLearningAdapter
from memory.contracts import MemoryOperationError, ProcessLearningCase
from memory.domain import MemoryScope
from memory.ports import LearningPort


class TestLegacyLearningAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = LegacyLearningAdapter()

    def test_adapter_implements_learning_port(self):
        self.assertIsInstance(self.adapter, LearningPort)

    @patch("ai.pipeline.process_case", new_callable=AsyncMock)
    async def test_case_processing_preserves_physical_group_scope(self, process):
        process.return_value = "job:1"
        command = ProcessLearningCase(
            scope=MemoryScope.bot(group_id=9, bot_id=5, actor_id="bot:5"),
            case_id="case:1",
        )
        self.assertEqual(await self.adapter.process_case(command), "job:1")
        process.assert_awaited_once_with("case:1", 9)

    async def test_personal_scope_cannot_enter_group_learning(self):
        command = ProcessLearningCase(
            scope=MemoryScope.personal(user_id=7, actor_id="user:7"), case_id="case:1")
        with self.assertRaisesRegex(MemoryOperationError, "group scope"):
            await self.adapter.process_case(command)


if __name__ == "__main__":
    unittest.main()
