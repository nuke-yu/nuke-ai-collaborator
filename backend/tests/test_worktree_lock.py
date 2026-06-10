"""Phase 3 承重墙：共享工作树 per-group 进程内互斥锁。

固定分片已确认（一群组同一时刻仅一个 worker 拥有），故进程内 asyncio.Lock 足够，
防同群组并发 git/build 撞 .git/index。
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from executors.plugins import workspace_tools as wt
from workspace import layout


class TestWorktreeLock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch("skills.constants.WORKSPACE_ROOT", Path(self._tmp.name).resolve())
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_lock_applies_under_group_worktree(self):
        async def go():
            wd = (layout.group_shared_dir(3) / "workspace" / "repo1").resolve()
            return wt._worktree_lock_for(wd, 3)
        self.assertIsInstance(asyncio.run(go()), asyncio.Lock)

    def test_no_lock_for_private_dir(self):
        async def go():
            wd = layout.bot_dir(3, 7).resolve()
            return wt._worktree_lock_for(wd, 3)
        self.assertIsNone(asyncio.run(go()))

    def test_no_lock_without_group(self):
        async def go():
            wd = Path("/tmp").resolve()
            return wt._worktree_lock_for(wd, None)
        self.assertIsNone(asyncio.run(go()))

    def test_same_group_same_lock(self):
        async def go():
            wd = (layout.group_shared_dir(3) / "workspace").resolve()
            a = wt._worktree_lock_for(wd, 3)
            b = wt._worktree_lock_for(wd, 3)
            wd9 = (layout.group_shared_dir(9) / "workspace").resolve()
            c = wt._worktree_lock_for(wd9, 9)
            return a, b, c
        a, b, c = asyncio.run(go())
        self.assertIs(a, b)
        self.assertIsNot(a, c)

    def test_serializes_concurrent_holders(self):
        # 两个协程争同一把锁：临界区不得交叠
        async def go():
            lock = None
            order = []

            async def worker(tag):
                nonlocal lock
                wd = (layout.group_shared_dir(3) / "workspace").resolve()
                lk = wt._worktree_lock_for(wd, 3)
                lock = lk
                async with lk:
                    order.append(f"{tag}-enter")
                    await asyncio.sleep(0.01)
                    order.append(f"{tag}-exit")

            await asyncio.gather(worker("A"), worker("B"))
            return order

        order = asyncio.run(go())
        # 每个 enter 后必须紧跟自己的 exit（无交叠）
        self.assertIn(order, [
            ["A-enter", "A-exit", "B-enter", "B-exit"],
            ["B-enter", "B-exit", "A-enter", "A-exit"],
        ])


if __name__ == "__main__":
    unittest.main()
