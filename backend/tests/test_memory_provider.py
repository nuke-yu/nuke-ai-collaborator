import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.memory_provider import (
    ChromaMemoryProvider,
    MemoryContext,
    MemoryEvent,
    NullMemoryProvider,
    get_memory_provider,
)


def _event(role="dev", bot_name="Bot"):
    return MemoryEvent(
        bot_id=5, group_id=9, role=role, bot_name=bot_name,
        message_id=42, text="切到 React 19", provider="claude", model="claude-opus-4-8",
    )


class TestNullMemoryProvider(unittest.IsolatedAsyncioTestCase):
    async def test_recall_empty_and_writes_noop(self):
        p = NullMemoryProvider()
        self.assertEqual(await p.recall(MemoryContext(5, 9, "dev", "q")), "")
        # observe / forget 不抛、无副作用
        self.assertIsNone(await p.observe(_event()))
        self.assertIsNone(await p.forget(5, 9))


class TestChromaMemoryProvider(unittest.IsolatedAsyncioTestCase):
    @patch("ai.memory.get_memory_context", new_callable=AsyncMock)
    async def test_recall_delegates(self, mock_ctx):
        mock_ctx.return_value = "记忆文本"
        out = await ChromaMemoryProvider().recall(MemoryContext(5, 9, "dev", "q", history=["h"]))
        self.assertEqual(out, "记忆文本")
        mock_ctx.assert_awaited_once_with(5, "dev", "q", 9, ["h"], None)

    @patch("ai.memory.get_memory_context", new_callable=AsyncMock)
    async def test_recall_forwards_thread_id(self, mock_ctx):
        """活跃讨论 topic 的 thread_id 要透传给 get_memory_context，供按 topic 召回摘要。"""
        mock_ctx.return_value = "x"
        await ChromaMemoryProvider().recall(
            MemoryContext(5, 9, "dev", "q", history=None, thread_id="disc:9:abc")
        )
        self.assertEqual(mock_ctx.await_args[0][5], "disc:9:abc")

    @patch("ai.memory.maybe_reflect", new_callable=AsyncMock)
    @patch("ai.memory.maybe_summarize", new_callable=AsyncMock)
    @patch("ai.memory.add_to_chroma", new_callable=AsyncMock)
    async def test_observe_forwards_thread_id_to_summarize(self, mock_add, mock_sum, mock_refl):
        """讨论中产出的摘要要带上 thread_id，写路径必须把它传给 maybe_summarize。"""
        ev = MemoryEvent(
            bot_id=5, group_id=9, role="dev", bot_name="Bot",
            message_id=42, text="切到 React 19", provider="claude", model="claude-opus-4-8",
            thread_id="disc:9:abc",
        )
        await ChromaMemoryProvider().observe(ev)
        self.assertEqual(mock_sum.await_args[0][4], "disc:9:abc")

    @patch("ai.memory.get_memory_context", new_callable=AsyncMock)
    async def test_recall_empty_role_passes_blank(self, mock_ctx):
        mock_ctx.return_value = ""
        await ChromaMemoryProvider().recall(MemoryContext(5, 9, "", "q"))
        # role 为空时传 ""（保持既有语义）
        self.assertEqual(mock_ctx.await_args[0][1], "")

    @patch("ai.memory.maybe_reflect", new_callable=AsyncMock)
    @patch("ai.memory.maybe_summarize", new_callable=AsyncMock)
    @patch("ai.memory.add_to_chroma", new_callable=AsyncMock)
    async def test_observe_fans_out_three_pipelines(self, mock_add, mock_sum, mock_refl):
        await ChromaMemoryProvider().observe(_event(role="dev", bot_name="Bot"))
        mock_add.assert_awaited_once_with(42, "切到 React 19", "dev", 5, 9, "claude", "claude-opus-4-8")
        mock_sum.assert_awaited_once_with(9, 5, "dev", [5], None)
        mock_refl.assert_awaited_once_with(9, 5, "dev", "claude", "claude-opus-4-8")

    @patch("ai.memory.maybe_reflect", new_callable=AsyncMock)
    @patch("ai.memory.maybe_summarize", new_callable=AsyncMock)
    @patch("ai.memory.add_to_chroma", new_callable=AsyncMock)
    async def test_observe_role_fallback_to_bot_name(self, mock_add, mock_sum, mock_refl):
        # role 为空：ingest 用 ""，summarize / reflect 回退到 bot_name
        await ChromaMemoryProvider().observe(_event(role="", bot_name="MemBot"))
        self.assertEqual(mock_add.await_args[0][2], "")
        self.assertEqual(mock_sum.await_args[0][2], "MemBot")
        self.assertEqual(mock_refl.await_args[0][2], "MemBot")

    @patch("ai.memory.maybe_reflect", new_callable=AsyncMock)
    @patch("ai.memory.maybe_summarize", new_callable=AsyncMock)
    @patch("ai.memory.add_to_chroma", new_callable=AsyncMock)
    async def test_observe_isolates_failing_pipeline(self, mock_add, mock_sum, mock_refl):
        # 一条子 pipeline 抛错不应冒泡，其余照常执行（与原先独立 bg.spawn 容错一致）
        mock_add.side_effect = RuntimeError("chroma boom")
        with self.assertLogs("ai.memory_provider", level="ERROR") as cm:
            await ChromaMemoryProvider().observe(_event())
        mock_sum.assert_awaited_once()
        mock_refl.assert_awaited_once()
        self.assertTrue(any("sub-pipeline failed" in line for line in cm.output))

    @patch("ai.memory.delete_bot_memory", new_callable=AsyncMock)
    async def test_forget_delegates(self, mock_del):
        await ChromaMemoryProvider().forget(5, 9)
        mock_del.assert_awaited_once_with(5, 9)


class TestFactory(unittest.IsolatedAsyncioTestCase):
    async def test_default_returns_chroma(self):
        self.assertIsInstance(get_memory_provider(None), ChromaMemoryProvider)
        self.assertIsInstance(get_memory_provider({"executor_config": {}}), ChromaMemoryProvider)

    async def test_off_policy_returns_null(self):
        for val in ("off", "none", "null", "disabled", "OFF"):
            p = get_memory_provider({"executor_config": {"memory": val}})
            self.assertIsInstance(p, NullMemoryProvider, f"{val} 应禁用记忆")


if __name__ == "__main__":
    unittest.main()
