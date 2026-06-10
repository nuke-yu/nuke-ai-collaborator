"""Phase 3: bot 指令面告知共享区写入约定（代码→workspace/，文档→docs/）。"""
import unittest

from workspace.templates import AGENT_TEMPLATE


class TestAgentTemplateConvention(unittest.TestCase):
    def test_template_renders_with_name_role(self):
        rendered = AGENT_TEMPLATE.format(name="小D", role="开发")
        self.assertIn("小D", rendered)
        self.assertIn("开发", rendered)

    def test_template_states_shared_write_convention(self):
        rendered = AGENT_TEMPLATE.format(name="X", role="Dev")
        self.assertIn("workspace/", rendered)   # 代码落点
        self.assertIn("docs/", rendered)        # 共享文档落点
        # 约定段落标题存在
        self.assertIn("工作区写入约定", rendered)


if __name__ == "__main__":
    unittest.main()
