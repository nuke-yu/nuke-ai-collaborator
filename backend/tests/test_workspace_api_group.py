"""API 边界在门口解析一次 group_id，并显式下传给 VFS（零新增查询）。"""
import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

import api.workspace as apiws


def _fake_get_db():
    @asynccontextmanager
    async def cm():
        yield object()
    return cm()


async def _fake_get_member(db, member_id):
    return {"id": member_id, "type": "bot", "group_id": 42, "role": "dev"}


class TestWorkspaceApiGroup(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            patch.object(apiws, "get_db", _fake_get_db),
            patch.object(apiws, "get_member", _fake_get_member),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_get_file_passes_group_id(self):
        captured = {}

        async def fake_read(member_id, path, group_id=None):
            captured["gid"] = group_id
            return "body"

        with patch.object(apiws, "read_file", fake_read):
            r = asyncio.run(apiws.get_workspace_file(7, "note.md"))
        self.assertEqual(captured["gid"], 42)
        self.assertEqual(r["content"], "body")

    def test_put_file_passes_group_id(self):
        captured = {}

        async def fake_write(member_id, path, content, group_id=None):
            captured["gid"] = group_id
            return "已写入"

        with patch.object(apiws, "write_file", fake_write):
            asyncio.run(apiws.put_workspace_file(7, {"path": "a.py", "content": "x"}))
        self.assertEqual(captured["gid"], 42)


if __name__ == "__main__":
    unittest.main()
