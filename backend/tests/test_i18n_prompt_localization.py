import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import skills.constants as _const
from workspace.layout import get_group_language, set_group_language, _GROUP_LANG_CACHE
from core.orchestration.prompt_builder import compile_system_prompt, build_system_prompt_base
from executors.plugins.tool_loop_v1_helpers import generate_thinking_preview


class TestI18nPromptLocalization(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self._tmp.name).resolve()
        self._patcher = patch("skills.constants.WORKSPACE_ROOT", self.workspace_root)
        self._patcher.start()
        
        self.group_id = 42
        # Clear cache for deterministic testing
        _GROUP_LANG_CACHE.clear()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()
        _GROUP_LANG_CACHE.clear()

    def test_group_language_cache_and_persistence(self):
        # 1. Default should be 'zh'
        self.assertEqual(get_group_language(self.group_id), "zh")
        
        # 2. Set to 'en' should update cache and write file
        set_group_language(self.group_id, "en")
        self.assertEqual(_GROUP_LANG_CACHE.get(self.group_id), "en")
        
        lang_file = self.workspace_root / f"group_{self.group_id}" / "lang.txt"
        self.assertTrue(lang_file.exists())
        self.assertEqual(lang_file.read_text(encoding="utf-8").strip(), "en")
        
        # 3. Clear cache and verify it reads back 'en' from file
        _GROUP_LANG_CACHE.clear()
        self.assertEqual(get_group_language(self.group_id), "en")
        self.assertEqual(_GROUP_LANG_CACHE.get(self.group_id), "en")

    def test_prompt_compilation_i18n(self):
        bot = {
            "id": 1,
            "name": "DevBot",
            "role": "developer",
            "system_prompt": "",
            "traits": []
        }
        
        class MockCtx:
            def __init__(self, group_id):
                self.group_id = group_id
                self.workflow_suffix = ""
                self.user_message = ""
                self.group_name = "TestGroup"
                self.group_announcement = "TestAnnouncement"
                self.all_members = [
                    {"name": "Alice", "type": "human"},
                    {"name": "BobBot", "type": "bot", "role": "Assistant"}
                ]

        # Test English Prompt compilation
        set_group_language(self.group_id, "en")
        prompt_en = build_system_prompt_base(bot, MockCtx(self.group_id), memory="test memory", always_section="", lang="en")
        
        self.assertIn("You are DevBot, developer.", prompt_en)
        self.assertIn("[Group Information]", prompt_en)
        self.assertIn("Group: TestGroup", prompt_en)
        self.assertIn("Announcement: TestAnnouncement", prompt_en)
        self.assertIn("Human Members: Alice", prompt_en)
        self.assertIn("AI Members: BobBot (Assistant)", prompt_en)
        self.assertIn("[Execution Environment]", prompt_en)
        self.assertIn("[Self-Learned Skill Rules]", prompt_en)
        self.assertIn("[LANGUAGE: The user's interface language is English.", prompt_en)
        self.assertNotIn("【运行环境】", prompt_en)
        self.assertNotIn("你是DevBot", prompt_en)

        # Test Chinese Prompt compilation
        set_group_language(self.group_id, "zh")
        prompt_zh = build_system_prompt_base(bot, MockCtx(self.group_id), memory="test memory", always_section="", lang="zh")
        
        self.assertIn("你是DevBot，developer。", prompt_zh)
        self.assertIn("【群组信息】", prompt_zh)
        self.assertIn("群组：TestGroup", prompt_zh)
        self.assertIn("公告：TestAnnouncement", prompt_zh)
        self.assertIn("人类成员：Alice", prompt_zh)
        self.assertIn("AI 成员：BobBot（Assistant）", prompt_zh)
        self.assertIn("【运行环境】", prompt_zh)
        self.assertIn("【自学技能规则】", prompt_zh)
        self.assertNotIn("[Group Information]", prompt_zh)
        self.assertNotIn("You are DevBot", prompt_zh)

    def test_thinking_preview_localization(self):
        class MockRunner:
            def __init__(self, group_id):
                self.ctx = MagicMock()
                self.ctx.group_id = group_id
                self.tool_records = []

        runner = MockRunner(self.group_id)

        # Test Chinese Thinking Preview
        set_group_language(self.group_id, "zh")
        preview_zh_1 = generate_thinking_preview(runner, 1)
        self.assertIn("分析用户需求和当前任务状态", preview_zh_1)
        
        preview_zh_2 = generate_thinking_preview(runner, 2)
        self.assertIn("上一步完成了 初步分析", preview_zh_2)

        # Iteration >= 3
        preview_zh_3 = generate_thinking_preview(runner, 3)
        self.assertIn("继续执行剩余任务，整合结果", preview_zh_3)

        # Non-empty tool records
        runner.tool_records = [{"name": "run_shell"}, {"name": "read_file"}]
        preview_zh_2_tools = generate_thinking_preview(runner, 2)
        self.assertIn("上一步完成了 run_shell, read_file", preview_zh_2_tools)

        # Test English Thinking Preview
        set_group_language(self.group_id, "en")
        preview_en_1 = generate_thinking_preview(runner, 1)
        self.assertIn("Analyzing user requirements and current task status", preview_en_1)
        
        # Test tool_records representation in English
        preview_en_2 = generate_thinking_preview(runner, 2)
        self.assertIn("Previous step completed run_shell, read_file", preview_en_2)

        # Iteration >= 3
        preview_en_3 = generate_thinking_preview(runner, 3)
        self.assertIn("Continuing with remaining tasks, consolidating results", preview_en_3)

    async def test_tool_visibility_by_skill_discovery(self):
        from executors.plugins.tool_loop_v1_helpers import setup_session
        from unittest.mock import AsyncMock, MagicMock, patch

        bot = {
            "id": 1,
            "name": "DevBot",
            "role": "developer",
            "system_prompt": "",
            "traits": [],
            "executor_config": {}
        }
        
        class MockCtx:
            def __init__(self, group_id):
                self.group_id = group_id
                self.workflow_suffix = ""
                self.user_message = "hello"
                self.sender = {"name": "User"}
                self.history = []
                self.is_workflow = False
                self.resume_session_id = None
                self.resume_messages = []
                self.file_url = None
                self.file_type = None
                self.ruleset = None
                self.group_name = "TestGroup"
                self.group_announcement = "TestAnnouncement"
                self.all_members = [
                    {"name": "Alice", "type": "human"},
                    {"name": "BobBot", "type": "bot", "role": "Assistant"}
                ]
                self.interaction = AsyncMock()

        ctx = MockCtx(self.group_id)

        class DummyRunner:
            def __init__(self):
                self.ctx = ctx
                self.bot = bot
                self.bot["avatar_color"] = "#000"
                self.executor = MagicMock()
                self.executor.executor_id = "test-executor"
                self.model_name = "test-model"
                self.provider = "test-provider"
                self.temperature = 0.7
                self.max_tokens = 2048
                self.memory = AsyncMock()
                self.memory.recall = AsyncMock(return_value="test memory")
                
                # Mock statically registered tools in executor manifest
                self.executor.manifest.tools = [
                    MagicMock(name="run_skill"),
                    MagicMock(name="read_file")
                ]
                self.executor.manifest.tools[0].name = "run_skill"
                self.executor.manifest.tools[1].name = "read_file"
                
                self.ruleset = None
                self.system_prompt_base = ""
                self.system_prompt = ""
                self.skills_xml = ""
                self.skills_snapshot = []
                self.always_skills = []
                self.tool_schemas = []
                self.messages = []
                self.session_id = "test-session"
                self.temp_id = "test-temp"

        def mock_get_schemas(names):
            all_schemas = [
                {"function": {"name": "run_skill"}},
                {"function": {"name": "read_file"}}
            ]
            return [s for s in all_schemas if s["function"]["name"] in names]

        # Mock dependencies in setup_session
        with patch("workspace.load_group_context", new=AsyncMock(return_value="")), \
             patch("core.workflow.current_thread_id", return_value="123"), \
             patch("executors.tool_router.router.has_providers", return_value=False), \
             patch("executors.tool_executor.get_schemas", side_effect=mock_get_schemas), \
             patch("core.orchestration.prompt_builder.compile_system_prompt", new=AsyncMock(return_value=("compiled prompt", "", [], []))):

            # Test Case 1: skill_discovery = True -> run_skill is in tool_schemas
            runner_true = DummyRunner()
            runner_true.executor.manifest.workspace.skill_discovery = True
            await setup_session(runner_true)
            
            tool_names_true = [s["function"]["name"] for s in runner_true.tool_schemas]
            self.assertIn("run_skill", tool_names_true)
            self.assertIn("read_file", tool_names_true)

            # Test Case 2: skill_discovery = False -> run_skill is NOT in tool_schemas
            runner_false = DummyRunner()
            runner_false.executor.manifest.workspace.skill_discovery = False
            await setup_session(runner_false)
            
            tool_names_false = [s["function"]["name"] for s in runner_false.tool_schemas]
            self.assertNotIn("run_skill", tool_names_false)
            self.assertIn("read_file", tool_names_false)

