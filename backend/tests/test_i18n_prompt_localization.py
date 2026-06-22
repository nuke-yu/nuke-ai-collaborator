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

        # Test English Thinking Preview
        set_group_language(self.group_id, "en")
        preview_en_1 = generate_thinking_preview(runner, 1)
        self.assertIn("Analyzing user requirements and current task status", preview_en_1)
        
        preview_en_2 = generate_thinking_preview(runner, 2)
        self.assertIn("Previous step completed initial analysis", preview_en_2)
