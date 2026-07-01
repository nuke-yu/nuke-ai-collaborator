"""API 边界在门口解析一次 group_id，并显式下传给 VFS（零新增查询）。"""
import asyncio
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
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

    def test_test_skill_group_layer_uses_group_shared_layout_path(self):
        captured = {}

        async def fake_list_skills_all(member_id, group_id=None, role=None):
            return [{"name": "group-skill", "layer": "group"}]

        def fake_skill_path(sdir, skill_name):
            captured["sdir"] = sdir
            skill_file = Path("/tmp/group-skill.md")
            skill_file.write_text("---\n---\nbody", encoding="utf-8")
            return skill_file, "md"

        async def fake_call_ai_once(**kwargs):
            return {"type": "text", "content": "ok"}

        with patch.object(apiws, "list_skills_all", fake_list_skills_all), \
             patch.object(apiws, "skill_path", fake_skill_path), \
             patch.object(apiws, "call_ai_once", fake_call_ai_once):
            resp = asyncio.run(apiws.test_skill(7, "group-skill", {"message": "hi"}))
        self.assertEqual(resp["response"], "ok")
        self.assertEqual(captured["sdir"], apiws.layout.group_shared_dir(42) / "skills")

    def test_test_skill_role_layer_uses_group_role_layout_path(self):
        captured = {}

        async def fake_list_skills_all(member_id, group_id=None, role=None):
            return [{"name": "role-skill", "layer": "role"}]

        def fake_skill_path(sdir, skill_name):
            captured["sdir"] = sdir
            skill_file = Path("/tmp/role-skill.md")
            skill_file.write_text("---\n---\nbody", encoding="utf-8")
            return skill_file, "md"

        async def fake_call_ai_once(**kwargs):
            return {"type": "text", "content": "ok"}

        with patch.object(apiws, "list_skills_all", fake_list_skills_all), \
             patch.object(apiws, "skill_path", fake_skill_path), \
             patch.object(apiws, "call_ai_once", fake_call_ai_once):
            resp = asyncio.run(apiws.test_skill(7, "role-skill", {"message": "hi"}))
        self.assertEqual(resp["response"], "ok")
        self.assertEqual(captured["sdir"], apiws.layout.group_roles_dir(42) / "dev" / "skills")


if __name__ == "__main__":
    unittest.main()
