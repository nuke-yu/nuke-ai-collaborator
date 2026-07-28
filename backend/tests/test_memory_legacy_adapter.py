import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.runtime import LegacyConversationMemoryAdapter
from memory.bootstrap import build_memory_client
from memory.contracts import ForgetMemory, MemoryOperationError, ObserveMemory, RecallMemory
from memory.domain import MemoryScope


class TestLegacyConversationMemoryAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = AsyncMock()
        self.adapter = LegacyConversationMemoryAdapter(self.provider)
        self.scope = MemoryScope.bot(
            group_id=9,
            bot_id=5,
            actor_id="worker:3",
            thread_id="disc:9:abc",
        )

    async def test_recall_translates_contract_and_marks_legacy_degradation(self):
        self.provider.recall.return_value = "memory context"
        result = await self.adapter.recall(
            RecallMemory(
                scope=self.scope,
                query="React version",
                metadata={"role": "dev", "history": ["previous"]},
            )
        )
        self.assertEqual(result.rendered_context, "memory context")
        self.assertTrue(result.degraded)
        ctx = self.provider.recall.await_args.args[0]
        self.assertEqual((ctx.bot_id, ctx.group_id, ctx.thread_id), (5, 9, "disc:9:abc"))
        self.assertEqual((ctx.role, ctx.query, ctx.history), ("dev", "React version", ["previous"]))

    @patch("ai.pipeline.enqueue_turn_observation", new_callable=AsyncMock)
    async def test_observe_durably_enqueues_stable_message_identity(self, enqueue):
        await self.adapter.observe(
            ObserveMemory(
                scope=self.scope, source_id="message:42",
                content="Use React 19", metadata={"message_id": 42},
            )
        )
        enqueue.assert_awaited_once_with(message_id=42, bot_id=5, group_id=9)
        self.provider.observe.assert_not_awaited()

    async def test_disabled_provider_does_not_enqueue_observation(self):
        self.provider.durable_observation_enabled = False
        with patch(
            "ai.pipeline.enqueue_turn_observation", new_callable=AsyncMock
        ) as enqueue:
            await self.adapter.observe(
                ObserveMemory(
                    scope=self.scope, source_id="message:42",
                    content="Use React 19", metadata={"message_id": 42},
                )
            )
        enqueue.assert_not_awaited()

    async def test_observe_rejects_missing_legacy_message_identity(self):
        with self.assertRaisesRegex(MemoryOperationError, "message_id"):
            await self.adapter.observe(
                ObserveMemory(scope=self.scope, source_id="event:1", content="content")
            )

    async def test_forget_never_widens_scope(self):
        await self.adapter.forget(ForgetMemory(scope=self.scope))
        self.provider.forget.assert_awaited_once_with(5, 9)
        with self.assertRaisesRegex(MemoryOperationError, "complete bot scope"):
            await self.adapter.forget(ForgetMemory(scope=self.scope, record_ids=("r1",)))

    async def test_legacy_adapter_rejects_non_bot_scope(self):
        group_scope = MemoryScope.group(group_id=9, actor_id="worker:3")
        with self.assertRaisesRegex(MemoryOperationError, "bot scope"):
            await self.adapter.recall(RecallMemory(scope=group_scope, query="q"))


class TestMemoryBootstrap(unittest.TestCase):
    @patch("ai.memory_provider.get_memory_provider")
    def test_composition_root_selects_legacy_provider(self, get_provider):
        provider = object()
        get_provider.return_value = provider
        client = build_memory_client({"executor_config": {"memory": "off"}})
        self.assertIsInstance(client, LegacyConversationMemoryAdapter)
        self.assertIs(client._provider, provider)
        get_provider.assert_called_once_with({"executor_config": {"memory": "off"}})


if __name__ == "__main__":
    unittest.main()
