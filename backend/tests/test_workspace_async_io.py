"""
tests/test_workspace_async_io.py — DFT-054 文件 I/O 不阻塞事件循环

Bot 工具的文件读写（read_file/write_file/read_local_file/write_local_file）原先
在事件循环线程内直接 Path.read_text/write_text，大文件会卡住整个 app 的 WS 投递。
改为经 asyncio.to_thread 下放到工作线程：验证阻塞调用发生在非循环线程，且结果正确。
"""
import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workspace as ws
from executors.plugins import workspace_tools as wt


class _ThreadSpy:
    """Records the thread id that the wrapped Path method runs on."""

    def __init__(self, method_name):
        self.method_name = method_name
        self.tid = None
        self._orig = getattr(Path, method_name)

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return lambda *a, **k: self(obj, *a, **k)

    def __call__(self, this, *args, **kwargs):
        self.tid = threading.get_ident()
        return self._orig(this, *args, **kwargs)


class TestLocalFileOffload(unittest.IsolatedAsyncioTestCase):

    async def test_read_local_file_runs_off_loop_thread(self):
        main_tid = threading.get_ident()
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.txt"
            f.write_text("hello", encoding="utf-8")
            spy = _ThreadSpy("read_text")
            ctx = {"group_id": 1}
            with patch.object(Path, "read_text", spy), \
                 patch("executors.plugins.workspace_tools._ws") as mock_ws:
                # Mock group_dir to return the temp dir so path validation passes
                mock_ws.group_dir.return_value = Path(d).resolve()
                result = await wt._handle_read_local_file(str(f), context=ctx)
        self.assertEqual(result, "hello")
        self.assertIsNotNone(spy.tid)
        self.assertNotEqual(spy.tid, main_tid)

    async def test_write_local_file_runs_off_loop_thread(self):
        main_tid = threading.get_ident()
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "sub" / "y.txt"
            spy = _ThreadSpy("write_text")
            ctx = {"group_id": 1}
            with patch.object(Path, "write_text", spy), \
                 patch("executors.plugins.workspace_tools._ws") as mock_ws:
                # Mock group_dir to return the temp dir so path validation passes
                mock_ws.group_dir.return_value = Path(d).resolve()
                result = await wt._handle_write_local_file(str(f), "data", context=ctx)
            self.assertIn("已写入", result)
            self.assertEqual(f.read_text(encoding="utf-8"), "data")
        self.assertNotEqual(spy.tid, main_tid)


class TestWorkspaceFileOffload(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # 路径真相源是 skills.constants.WORKSPACE_ROOT（layout 实时读取它）；
        # 同时保留 ws.WORKSPACE_ROOT 以覆盖 workspace 内直接引用（archive_run 等）。
        root = Path(self._tmp.name).resolve()
        self._patchers = [
            patch("skills.constants.WORKSPACE_ROOT", root),
            patch.object(ws, "WORKSPACE_ROOT", root),
        ]
        for p in self._patchers:
            p.start()

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    async def test_read_file_runs_off_loop_thread(self):
        main_tid = threading.get_ident()
        await ws.write_file(7, "note.md", "body")
        spy = _ThreadSpy("read_text")
        with patch.object(Path, "read_text", spy):
            result = await ws.read_file(7, "note.md")
        self.assertEqual(result, "body")
        self.assertNotEqual(spy.tid, main_tid)

    async def test_write_file_runs_off_loop_thread(self):
        main_tid = threading.get_ident()
        spy = _ThreadSpy("write_text")
        with patch.object(Path, "write_text", spy):
            result = await ws.write_file(7, "note.md", "body")
        self.assertIn("已写入", result)
        self.assertEqual((Path(self._tmp.name) / "bot_7" / "note.md").read_text(encoding="utf-8"), "body")
        self.assertNotEqual(spy.tid, main_tid)

    async def test_write_file_does_not_block_loop(self):
        """While a slow write is in flight, other coroutines keep running."""
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(20):
                await asyncio.sleep(0.01)
                ticks += 1

        import time
        orig = Path.write_text

        def slow_write(this, *a, **k):
            time.sleep(0.2)
            return orig(this, *a, **k)

        t = asyncio.create_task(ticker())
        with patch.object(Path, "write_text", slow_write):
            await ws.write_file(7, "note.md", "body")
        # loop kept ticking during the 0.2s blocking write → offloaded
        self.assertGreater(ticks, 0)
        await t

    async def test_write_file_logs_when_history_save_fails(self):
        await ws.write_file(7, "note.md", "v1")

        with patch("workspace._save_to_history", side_effect=RuntimeError("history failed")), \
             self.assertLogs("workspace", level="ERROR") as logs:
            result = await ws.write_file(7, "note.md", "v2")

        self.assertIn("已写入", result)
        self.assertTrue(any("failed to save history" in line for line in logs.output))


class TestLoadGroupContext(unittest.IsolatedAsyncioTestCase):
    async def test_returns_shared_tree_and_key_docs(self):
        import tempfile, pathlib
        from skills import constants as _c
        orig = _c.WORKSPACE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _c.WORKSPACE_ROOT = pathlib.Path(tmp)
            try:
                import workspace as ws
                # scaffold shared workspace
                shared = ws.group_workspace(1)                  # creates group_1/shared
                (shared / "workspace" / "my-app").mkdir(parents=True)
                (shared / "workspace" / "my-app" / "app.js").write_text("// hi")
                (shared / "BOARD.md").write_text("# Board")
                (shared / "workspace" / "PROJECTS.md").write_text("# Projects")

                result = await ws.load_group_context(1)

                self.assertIn("my-app", result)
                self.assertIn("app.js", result)
                self.assertIn("# Board", result)
                self.assertIn("# Projects", result)
                self.assertIn("【共享工作区目录】", result)
                self.assertIn("【工作看板】", result)
                self.assertIn("【项目清单】", result)
            finally:
                _c.WORKSPACE_ROOT = orig

    async def test_empty_workspace_returns_no_key_docs(self):
        import tempfile, pathlib
        from skills import constants as _c
        orig = _c.WORKSPACE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _c.WORKSPACE_ROOT = pathlib.Path(tmp)
            try:
                import workspace as ws
                ws.group_workspace(99)   # creates the dir, but nothing inside
                result = await ws.load_group_context(99)
                # no key docs exist → no doc sections; tree may be empty
                self.assertIsInstance(result, str)
                self.assertNotIn("【工作看板】", result)
                self.assertNotIn("【项目清单】", result)
                self.assertNotIn("【需求文档】", result)
            finally:
                _c.WORKSPACE_ROOT = orig

    async def test_load_group_context_logs_when_markdown_read_fails(self):
        import tempfile, pathlib
        from skills import constants as _c
        orig = _c.WORKSPACE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _c.WORKSPACE_ROOT = pathlib.Path(tmp)
            try:
                import workspace as ws
                shared = ws.group_workspace(1)
                board = shared / "BOARD.md"
                board.write_text("# Board", encoding="utf-8")

                orig_read_text = Path.read_text

                def fake_read_text(self, *args, **kwargs):
                    if self == board:
                        raise OSError("read failed")
                    return orig_read_text(self, *args, **kwargs)

                with patch("pathlib.Path.read_text", new=fake_read_text), \
                     self.assertLogs("workspace", level="WARNING") as logs:
                    result = await ws.load_group_context(1)

                self.assertIsInstance(result, str)
                self.assertTrue(any("failed to read markdown file" in line for line in logs.output))
            finally:
                _c.WORKSPACE_ROOT = orig


if __name__ == "__main__":
    unittest.main()
