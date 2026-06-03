import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _writer_mod

_TEST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_rd_tools.db")


class TestJiraStub(unittest.IsolatedAsyncioTestCase):
    """Jira 替身：工单落本地 tickets 表，create/list 回环。"""

    async def asyncSetUp(self):
        # DB_PATH 是模块全局，别的测试也会改 —— 在 setUp 里夺取并在 tearDown 还原，
        # 保证整套运行时本测试期间 get_db()/write_connect() 都指向本测试库。
        self._orig_db = database.DB_PATH
        self._orig_writer = _writer_mod.DB_PATH
        database.DB_PATH = _TEST_DB
        _writer_mod.DB_PATH = _TEST_DB
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self._orig_db
        _writer_mod.DB_PATH = self._orig_writer
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_create_and_list_tickets(self):
        from integrations.jira import LocalJiraClient
        jira = LocalJiraClient()
        t1 = await jira.create_ticket(1, "登录页", "做个登录页", "AC: 能登录")
        t2 = await jira.create_ticket(1, "仪表盘", "做个看板", "AC: 显示数据")
        self.assertEqual(t1["ticket_id"], "DFT-1")
        self.assertEqual(t2["ticket_id"], "DFT-2")

        items = await jira.list_tickets(1)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "登录页")
        self.assertEqual(items[0]["acceptance_criteria"], "AC: 能登录")
        self.assertEqual(items[0]["status"], "backlog")
        # 群隔离：别的群看不到
        self.assertEqual(await jira.list_tickets(2), [])

    async def test_create_jira_tool_handler(self):
        from executors.plugins.rd_tools import _create_jira, _list_jira
        out = await _create_jira("登录页", "描述", "AC: x", context={"group_id": 1})
        self.assertIn("DFT-1", out)
        self.assertIn("登录页", out)
        listed = await _list_jira(context={"group_id": 1})
        self.assertIn("DFT-1", listed)
        self.assertIn("AC: x", listed)


class TestGitStub(unittest.IsolatedAsyncioTestCase):
    """PR 替身：产出工件并返回 stub pr_id/url，关联工单号。"""

    async def test_create_pr_returns_stub_and_writes_artifact(self):
        from integrations.git import LocalGitClient
        with patch("integrations.git.write_file", new=AsyncMock()) as wf:
            git = LocalGitClient()
            pr = await git.create_pr(1, "实现登录", "改了 auth.py", ["DFT-1"])
            self.assertEqual(pr["pr_id"], "PR-1")
            self.assertEqual(pr["tickets"], ["DFT-1"])
            self.assertTrue(pr["url"].endswith("PR-1.md"))
            # 第二个 PR 自增
            pr2 = await git.create_pr(1, "修复", "", ["DFT-2"])
            self.assertEqual(pr2["pr_id"], "PR-2")
        # 工件写了两次
        self.assertEqual(wf.await_count, 2)

    async def test_create_pr_tool_handler(self):
        from executors.plugins import rd_tools
        with patch.object(rd_tools, "get_git") as gg:
            gg.return_value.create_pr = AsyncMock(return_value={
                "pr_id": "PR-1", "url": "local://prs/PR-1.md", "title": "t", "tickets": ["DFT-1"]})
            out = await rd_tools._create_pr("实现登录", "desc", ["DFT-1"], context={"group_id": 1})
        self.assertIn("PR-1", out)
        self.assertIn("DFT-1", out)


class TestRdToolsRegistration(unittest.TestCase):
    """RD 工具进了 executor 的 manifest，且权限钩子里被 auto-allow。"""

    def test_tools_in_manifest_and_auto_allowed(self):
        from executors.plugins.tool_loop_v1 import ToolLoopV1
        names = {t.name for t in ToolLoopV1.manifest.tools}
        self.assertIn("create_jira_ticket", names)
        self.assertIn("list_jira_tickets", names)
        self.assertIn("create_pr", names)
        from executors.plugins.workspace_tools import _AUTO_ALLOW_TOOLS
        self.assertIn("create_jira_ticket", _AUTO_ALLOW_TOOLS)
        self.assertIn("create_pr", _AUTO_ALLOW_TOOLS)


if __name__ == "__main__":
    unittest.main()
