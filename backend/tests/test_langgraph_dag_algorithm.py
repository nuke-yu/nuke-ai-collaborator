"""Unit tests for Task 10 (LangGraph Learning DAG Checkpoint Engine and Adapter)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (LangGraphDAGAlgorithmAdapter,
                                         LangGraphDAGEngine)


class TestLangGraphDAGEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = LangGraphDAGEngine()

    def test_create_checkpoint_hashes_state(self) -> None:
        chk = self.engine.create_checkpoint(
            thread_id="th:1",
            step_name="assemble_case",
            state={"run_id": "r:100", "task": "fix bug"},
        )
        self.assertTrue(chk.checkpoint_id.startswith("chk:th:1:assemble_case:"))
        self.assertEqual(len(chk.state_hash), 16)
        self.assertEqual(chk.state_payload["run_id"], "r:100")

    def test_verify_checkpoint_chain_validates_lineage(self) -> None:
        chk1 = self.engine.create_checkpoint("th:1", "step1", {"step": 1})
        chk2 = self.engine.create_checkpoint("th:1", "step2", {"step": 2}, parent_id=chk1.checkpoint_id)

        self.assertTrue(self.engine.verify_checkpoint_chain([chk1, chk2]))

    def test_verify_checkpoint_chain_rejects_broken_parent_link(self) -> None:
        chk1 = self.engine.create_checkpoint("th:1", "step1", {"step": 1})
        chk2 = self.engine.create_checkpoint("th:1", "step2", {"step": 2}, parent_id="non_existent_chk")

        self.assertFalse(self.engine.verify_checkpoint_chain([chk1, chk2]))

    def test_verify_checkpoint_chain_rejects_tampered_state_and_cross_thread_parent(self) -> None:
        parent = self.engine.create_checkpoint("thread-a", "one", {"x": 1})
        child = self.engine.create_checkpoint("thread-a", "two", {"x": 2}, parent.checkpoint_id)
        tampered = child.__class__(child.checkpoint_id, child.thread_id, child.parent_checkpoint_id,
                                   child.step_name, child.state_hash, {"x": 99}, child.created_at)
        self.assertFalse(self.engine.verify_checkpoint_chain([parent, tampered]))
        other = self.engine.create_checkpoint("thread-b", "two", {"x": 2}, parent.checkpoint_id)
        self.assertFalse(self.engine.verify_checkpoint_chain([parent, other]))


class TestLangGraphDAGAlgorithmAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = LangGraphDAGAlgorithmAdapter()

    async def test_adapter_descriptor_matches_audit_policy(self) -> None:
        self.assertEqual(self.adapter.descriptor.algorithm_id, "nuke.langgraph.dag_checkpoint")
        self.assertEqual(self.adapter.descriptor.source, "LangGraph (LangChain / MIT)")
        self.assertEqual(self.adapter.descriptor.license, "MIT")

    async def test_adapter_checkpoint_returns_checkpoint(self) -> None:
        chk = await self.adapter.checkpoint("th:2", "distill", {"status": "ok"})
        self.assertTrue(chk.checkpoint_id.startswith("chk:th:2:distill:"))

    async def test_saver_compat_put_get_writes_and_delete(self) -> None:
        chk = await self.adapter.put("th:3", "step", {"value": 1})
        await self.adapter.put_writes(chk.checkpoint_id, "task:1", {"channel": "ok"})
        self.assertEqual((await self.adapter.get_tuple("th:3")).checkpoint_id, chk.checkpoint_id)
        self.assertEqual((await self.adapter.pending_writes(chk.checkpoint_id))[0]["channel"], "channel")
        self.assertEqual(await self.adapter.delete_thread("th:3"), 1)


if __name__ == "__main__":
    unittest.main()
