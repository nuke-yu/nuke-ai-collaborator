"""工作区工具包装层从 context 贯穿 group_id 给 VFS。"""
import asyncio
import unittest

from executors.plugins import workspace_tools as wt


class TestToolGroupThreading(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_write_file_threads_group_id(self):
        captured = {}

        async def fake_write(bot_id, path, content, group_id=None):
            captured["bot_id"] = bot_id
            captured["gid"] = group_id
            return "ok"

        orig = wt._ws.write_file
        wt._ws.write_file = fake_write
        try:
            self._run(wt._handle_write_file("a.py", "x", context={"bot_id": 7, "group_id": 3}))
        finally:
            wt._ws.write_file = orig
        self.assertEqual(captured, {"bot_id": 7, "gid": 3})

    def test_read_file_threads_group_id(self):
        captured = {}

        async def fake_read(bot_id, path, offset=None, limit=None, group_id=None):
            captured["gid"] = group_id
            return "body"

        orig = wt._ws.read_file
        wt._ws.read_file = fake_read
        try:
            self._run(wt._handle_read_file("a.py", context={"bot_id": 7, "group_id": 3}))
        finally:
            wt._ws.read_file = orig
        self.assertEqual(captured["gid"], 3)

    def test_edit_file_threads_group_id(self):
        captured = {}

        async def fake_edit(bot_id, path, old, new, replace_all=False, group_id=None):
            captured["gid"] = group_id
            return "ok"

        orig = wt._ws.edit_file
        wt._ws.edit_file = fake_edit
        try:
            self._run(wt._handle_edit_file("a.py", "o", "n", context={"bot_id": 7, "group_id": 3}))
        finally:
            wt._ws.edit_file = orig
        self.assertEqual(captured["gid"], 3)

    def test_list_workspace_threads_group_id(self):
        captured = {}

        async def fake_list(bot_id, group_id=None):
            captured["gid"] = group_id
            return ""

        orig = wt._ws.list_workspace
        wt._ws.list_workspace = fake_list
        try:
            self._run(wt._handle_list_workspace(context={"bot_id": 7, "group_id": 3}))
        finally:
            wt._ws.list_workspace = orig
        self.assertEqual(captured["gid"], 3)


if __name__ == "__main__":
    unittest.main()
