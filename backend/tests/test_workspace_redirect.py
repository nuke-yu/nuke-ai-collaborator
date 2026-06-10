"""
工作区共享重定向（_get_effective_ws）。

Phase 2 改造后：
- group_id 由调用方显式传入，路径解析不再查 DB（删除 SELECT group_id FROM members）。
- 共享文件名 + 共享前缀（workspace/ docs/ prs/）→ 群组 shared 区。
- 私有路径 → 嵌套 group_{gid}/bots/bot_{id}。
- deliverables/ 不再特殊（落私有区）。
"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from workspace import _get_effective_ws, layout


class TestWorkspaceRedirect(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch.object(layout, "WORKSPACE_ROOT", Path(self._tmp.name))
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_shared_files_and_prefixes_redirect_to_group(self):
        cases = ["BOARD.md", "SPEC.md", "API_CONTRACT.md", "RETRO_LATEST.md",
                 "workspace/repo1/main.py", "docs/qa-report.md", "prs/12.md"]
        for shared in cases:
            with self.subTest(path=shared):
                r = _get_effective_ws(7, shared, group_id=3)
                self.assertEqual(r.resolve(), layout.group_shared_dir(3).resolve())

    def test_private_path_stays_private_nested(self):
        r = _get_effective_ws(7, "notes.md", group_id=3)
        self.assertEqual(r.resolve(), layout.bot_dir(3, 7).resolve())

    def test_deliverables_no_longer_special(self):
        # deliverables/ 不再重定向到共享区，落私有
        r = _get_effective_ws(7, "deliverables/app.py", group_id=3)
        self.assertEqual(r.resolve(), layout.bot_dir(3, 7).resolve())

    def test_no_db_query_when_group_id_given(self):
        # group_id 显式给出 → 绝不查 DB（connect_sync 被调用即失败）
        with patch("db.connect_sync", side_effect=AssertionError("must not query DB")):
            r = _get_effective_ws(7, "BOARD.md", group_id=3)
        self.assertEqual(r.resolve(), layout.group_shared_dir(3).resolve())


if __name__ == "__main__":
    unittest.main()
