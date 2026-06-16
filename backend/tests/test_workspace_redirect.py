"""
工作区路由（_get_effective_ws）—— 共享优先。

- group_id 由调用方显式传入，路径解析不再查 DB（删除 SELECT group_id FROM members）。
- 默认共享：有 group_id 且路径不在私有命名空间 → 群组 shared 区（含无前缀的项目/文档路径）。
- 私有命名空间（skills/ logs/ 前缀，或 bot 身份/记忆文件）→ 嵌套 group_{gid}/bots/bot_{id}。
- group_id=None（无群组上下文）→ 一律落 bot 私有（扁平）。
"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from workspace import _get_effective_ws, layout


class TestWorkspaceRedirect(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch("skills.constants.WORKSPACE_ROOT", Path(self._tmp.name).resolve())
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_shared_files_and_prefixes_redirect_to_group(self):
        cases = ["BOARD.md", "SPEC.md", "API_CONTRACT.md", "RETRO_LATEST.md",
                 "workspace/repo1/main.py", "docs/qa-report.md", "prs/12.md"]
        for shared in cases:
            with self.subTest(path=shared):
                r, _ = _get_effective_ws(7, shared, group_id=3)
                self.assertEqual(r.resolve(), layout.group_shared_dir(3).resolve())

    def test_bare_path_defaults_to_shared(self):
        # 默认共享：无前缀的项目/文档路径不再静默落私有，落群组 shared
        for p in ["notes.md", "pacman/index.html", "deliverables/app.py", "src/main.py"]:
            with self.subTest(path=p):
                r, _ = _get_effective_ws(7, p, group_id=3)
                self.assertEqual(r.resolve(), layout.group_shared_dir(3).resolve())

    def test_private_namespace_stays_private(self):
        # bot 身份/记忆/私有技能/日志 必须留私有区（群组隔离不变量 + startup 读自身文件）
        cases = ["IDENTITY.md", "SOUL.md", "BOOTSTRAP.md", "AGENT.md", "MEMORY.md",
                 "skills/learned/draft/x.md", "logs/2026-06-16.md"]
        for p in cases:
            with self.subTest(path=p):
                r, _ = _get_effective_ws(7, p, group_id=3)
                self.assertEqual(r.resolve(), layout.bot_dir(3, 7).resolve())

    def test_no_group_context_stays_private(self):
        # 无群组上下文 → 私有（扁平），不查 DB
        r, _ = _get_effective_ws(7, "pacman/index.html", group_id=None)
        self.assertEqual(r.resolve(), layout.bot_dir(None, 7).resolve())

    def test_no_db_query_when_group_id_given(self):
        # group_id 显式给出 → 绝不查 DB（connect_sync 被调用即失败）
        with patch("db.connect_sync", side_effect=AssertionError("must not query DB")):
            r, _ = _get_effective_ws(7, "BOARD.md", group_id=3)
        self.assertEqual(r.resolve(), layout.group_shared_dir(3).resolve())


if __name__ == "__main__":
    unittest.main()
