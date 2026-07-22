import hashlib
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.runtime import LegacyLearningAdapter, LegacyPipelineJobAdapter
from memory.contracts import (AssembleCase, ClaimPipelineJob, CompleteExperienceUsage,
                              CompletePipelineJob, CompleteSkillUsage, EnqueuePipelineJob,
                              FailPipelineJob, MemoryOperationError, ProcessLearningCase,
                              RecallExperiences, RecallSkills)
from memory.domain import MemoryScope
from memory.ports import LearningPort, PipelineJobRepositoryPort


class TestLegacyLearningAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = LegacyLearningAdapter()
        self.pipeline_repo = LegacyPipelineJobAdapter()

    def test_adapter_implements_learning_port(self):
        self.assertIsInstance(self.adapter, LearningPort)
        self.assertIsInstance(self.pipeline_repo, PipelineJobRepositoryPort)

    @patch("ai.pipeline.process_case", new_callable=AsyncMock)
    async def test_case_processing_preserves_physical_group_scope(self, process):
        process.return_value = "job:1"
        command = ProcessLearningCase(
            scope=MemoryScope.bot(group_id=9, bot_id=5, actor_id="bot:5"),
            case_id="case:1",
            input_version="2",
        )
        self.assertEqual(await self.adapter.process_case(command), "job:1")
        process.assert_awaited_once_with("case:1", 9, input_version="2")

    @patch("ai.cases.assemble_case", new_callable=AsyncMock)
    async def test_case_assembly_routes_through_scope(self, assemble):
        assemble.return_value = "case:1"
        command = AssembleCase(
            scope=MemoryScope.bot(group_id=9, bot_id=5, actor_id="bot:5"),
            run_id="run:1",
            task="do stuff",
            outcome="completed",
            tool_records=({"name": "read_file"},),
        )
        self.assertEqual(await self.adapter.assemble_case(command), "case:1")
        assemble.assert_awaited_once_with(
            run_id="run:1", group_id=9, bot_id=5, task="do stuff", outcome="completed", tool_records=[{"name": "read_file"}]
        )

    @patch("ai.experiences.recall_experiences", new_callable=AsyncMock)
    async def test_recall_experiences_routes_through_scope(self, recall):
        recall.return_value = ("[exp]", ["exp:1"])
        command = RecallExperiences(
            scope=MemoryScope.bot(group_id=9, bot_id=5, actor_id="bot:5"),
            query="fix error",
            run_id="run:1",
        )
        self.assertEqual(await self.adapter.recall_experiences(command), ("[exp]", ["exp:1"]))
        recall.assert_awaited_once_with(
            query="fix error", run_id="run:1", group_id=9, bot_id=5, limit=2, char_budget=2400
        )

    @patch("ai.skill_learning.recall_skills", new_callable=AsyncMock)
    async def test_recall_skills_routes_through_scope(self, recall):
        recall.return_value = ("[skill]", ["skill:1"])
        command = RecallSkills(
            scope=MemoryScope.bot(group_id=9, bot_id=5, actor_id="bot:5"),
            query="fix error",
            run_id="run:1",
        )
        self.assertEqual(await self.adapter.recall_skills(command), ("[skill]", ["skill:1"]))
        recall.assert_awaited_once_with(
            query="fix error", run_id="run:1", group_id=9, bot_id=5, limit=2
        )

    async def test_personal_scope_cannot_enter_group_learning(self):
        command = ProcessLearningCase(
            scope=MemoryScope.personal(user_id=7, actor_id="user:7"), case_id="case:1")
        with self.assertRaisesRegex(MemoryOperationError, "group scope"):
            await self.adapter.process_case(command)

    async def test_pipeline_job_identity_remains_upgrade_compatible(self):
        mock_db = AsyncMock()
        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value = mock_db
        scope = MemoryScope.group(group_id=9, actor_id="pipeline")
        canonical_key = "evaluate_case:9:case:42:3"
        expected_id = "job:" + hashlib.sha256(canonical_key.encode()).hexdigest()[:24]

        with patch("ai.memory._memory_db", return_value=mock_db_ctx):
            job_id = await self.pipeline_repo.enqueue(
                scope, "evaluate_case", "case:42", input_version="3"
            )

        self.assertEqual(job_id, expected_id)
        params = mock_db.execute.await_args.args[1]
        self.assertEqual(params[0], expected_id)
        self.assertEqual(params[5], canonical_key)


if __name__ == "__main__":
    unittest.main()
