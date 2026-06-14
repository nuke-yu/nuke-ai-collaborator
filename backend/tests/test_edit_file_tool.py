"""edit_file 工具层 glue + 截断提示接线测试。

editing 子系统的纯逻辑在 editing/tests/ 已覆盖；这里只验证工具层把
read(workspace) → apply_replacement(editing) → write(workspace) 接对了，
以及截断提示走的是 editing.build_completion_hint（不再引用 replace_file_content）。
"""
import asyncio
import unittest
from unittest.mock import patch, AsyncMock

from executors.plugins import workspace_tools as wt
from executors import tool_executor as te


def _run(coro):
    return asyncio.run(coro)


class TestEditFileHandler(unittest.TestCase):
    def test_edit_file_delegates_to_workspace_edit(self):
        with patch.object(wt._ws, "edit_file", new=AsyncMock(return_value="已修改 a.py")) as e:
            out = _run(wt._handle_edit_file("a.py", "x = 1", "x = 99", context={"bot_id": 1}))
        self.assertEqual(out, "已修改 a.py")
        e.assert_awaited_once_with(1, "a.py", "x = 1", "x = 99",
                                   replace_all=False, group_id=None, edits=None)

    def test_edit_file_batch_threads_edits(self):
        edits = [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "d"}]
        with patch.object(wt._ws, "edit_file", new=AsyncMock(return_value="批量完成")) as e:
            out = _run(wt._handle_edit_file("a.py", edits=edits, context={"bot_id": 1}))
        self.assertEqual(out, "批量完成")
        e.assert_awaited_once_with(1, "a.py", None, None,
                                   replace_all=False, group_id=None, edits=edits)

    def test_edit_file_requires_old_new_or_edits(self):
        out = _run(wt._handle_edit_file("a.py", context={"bot_id": 1}))
        self.assertIn("参数错误", out)

    def test_read_anchored_delegates(self):
        with patch.object(wt._ws, "read_anchored", new=AsyncMock(return_value="L1#abc│ x")) as e:
            out = _run(wt._handle_read_anchored("a.py", context={"bot_id": 1}))
        self.assertEqual(out, "L1#abc│ x")
        e.assert_awaited_once_with(1, "a.py", group_id=None)

    def test_edit_anchored_delegates(self):
        edits = [{"anchor": "L1#abc", "text": "y"}]
        with patch.object(wt._ws, "edit_anchored", new=AsyncMock(return_value="锚点完成")) as e:
            out = _run(wt._handle_edit_anchored("a.py", edits=edits, context={"bot_id": 1}))
        self.assertEqual(out, "锚点完成")
        e.assert_awaited_once_with(1, "a.py", edits, group_id=None)

    def test_edit_anchored_requires_edits(self):
        out = _run(wt._handle_edit_anchored("a.py", context={"bot_id": 1}))
        self.assertIn("参数错误", out)

    def test_edit_file_missing_bot_id(self):
        out = _run(wt._handle_edit_file("a.py", "a", "b", context={}))
        self.assertIn("缺少 bot_id", out)

    def test_edit_file_propagates_read_error(self):
        with patch.object(wt._ws, "edit_file", new=AsyncMock(return_value="[文件不存在] a.py")):
            out = _run(wt._handle_edit_file("a.py", "a", "b", context={"bot_id": 1}))
        self.assertIn("文件不存在", out)

    def test_edit_file_not_found_returns_edit_failure(self):
        with patch.object(wt._ws, "edit_file", new=AsyncMock(return_value="[编辑失败] old_string 在文件中未找到")) as e:
            out = _run(wt._handle_edit_file("a.py", "nope", "b", context={"bot_id": 1}))
        self.assertIn("编辑失败", out)
        e.assert_awaited_once()


class TestTruncationHintWiring(unittest.TestCase):
    def test_truncated_write_uses_edit_file_hint(self):
        from executors.base import ToolDef

        async def handler(path, content):
            return "完成"

        te.register(ToolDef(name="write_file", description="", parameters={}), handler)
        try:
            result, is_error = _run(te.execute(
                "write_file", {"path": "big.html", "content": "abc", "__truncated__": True}
            ))
        finally:
            te._registry.pop("write_file", None)
        self.assertIn("edit_file", result)
        self.assertNotIn("replace_file_content", result)


if __name__ == "__main__":
    unittest.main()
