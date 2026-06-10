"""一次性工作区布局迁移：workspaces/bot_{id} → workspaces/group_{gid}/bots/bot_{id}。

幂等 + dry-run 孤儿保护 + 收尾校验。核心逻辑吃显式 bots 列表，不依赖 DB。
"""
import tempfile
import unittest
from pathlib import Path

from scripts import migrate_workspace_layout as mig


def _touch_bot_dir(root: Path, bot_id: int) -> Path:
    d = root / f"bot_{bot_id}"
    (d / "skills").mkdir(parents=True)
    (d / "IDENTITY.md").write_text("id", encoding="utf-8")
    return d


class TestMigrateLayout(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_moves_known_bot(self):
        _touch_bot_dir(self.root, 7)
        report = mig.apply_migration(self.root, [(7, 3)], dry_run=False)
        nested = self.root / "group_3" / "bots" / "bot_7"
        self.assertTrue((nested / "IDENTITY.md").exists())
        self.assertFalse((self.root / "bot_7").exists())
        self.assertEqual(report["moved"], [(7, 3)])

    def test_dry_run_moves_nothing(self):
        _touch_bot_dir(self.root, 7)
        report = mig.apply_migration(self.root, [(7, 3)], dry_run=True)
        self.assertTrue((self.root / "bot_7").exists())
        self.assertFalse((self.root / "group_3" / "bots" / "bot_7").exists())
        self.assertEqual(report["moved"], [(7, 3)])  # 计划要移动，但 dry-run 未执行

    def test_idempotent_when_target_exists(self):
        _touch_bot_dir(self.root, 7)
        mig.apply_migration(self.root, [(7, 3)], dry_run=False)
        # 再跑一次：源已无、目标已在 → 跳过，不报错
        report = mig.apply_migration(self.root, [(7, 3)], dry_run=False)
        self.assertEqual(report["moved"], [])
        self.assertIn((7, 3), report["already"])

    def test_orphan_listed_not_deleted_by_default(self):
        _touch_bot_dir(self.root, 999)   # 无 DB 记录
        report = mig.apply_migration(self.root, [(7, 3)], dry_run=False)
        self.assertIn(999, report["orphans"])
        self.assertTrue((self.root / "bot_999").exists())   # 默认不删

    def test_delete_orphans_flag_removes_them(self):
        _touch_bot_dir(self.root, 999)
        report = mig.apply_migration(self.root, [(7, 3)], dry_run=False, delete_orphans=True)
        self.assertIn(999, report["orphans"])
        self.assertFalse((self.root / "bot_999").exists())

    def test_verify_passes_after_migration(self):
        _touch_bot_dir(self.root, 7)
        _touch_bot_dir(self.root, 8)
        mig.apply_migration(self.root, [(7, 3), (8, 3)], dry_run=False)
        ok, problems = mig.verify(self.root, [(7, 3), (8, 3)])
        self.assertTrue(ok, problems)

    def test_verify_fails_if_flat_remains(self):
        _touch_bot_dir(self.root, 7)   # 未迁移
        ok, problems = mig.verify(self.root, [(7, 3)])
        self.assertFalse(ok)

    def test_conflict_listed_separately_from_already(self):
        # 扁平与嵌套并存（双写 bug 后果）→ conflicts，不归入 already
        _touch_bot_dir(self.root, 7)
        nested = self.root / "group_3" / "bots" / "bot_7"
        nested.mkdir(parents=True)
        (nested / "AGENT.md").write_text("live", encoding="utf-8")  # 嵌套的活文件
        report = mig.apply_migration(self.root, [(7, 3)], dry_run=True)
        self.assertIn((7, 3), report["conflicts"])
        self.assertNotIn((7, 3), report["already"])
        self.assertEqual(report["moved"], [])

    def test_merge_fills_missing_keeps_nested_and_drops_flat(self):
        _touch_bot_dir(self.root, 7)   # flat: IDENTITY.md("id") + skills/
        (self.root / "bot_7" / "AGENT.md").write_text("flat-agent", encoding="utf-8")
        nested = self.root / "group_3" / "bots" / "bot_7"
        nested.mkdir(parents=True)
        (nested / "AGENT.md").write_text("live-agent", encoding="utf-8")  # 嵌套已有，保留
        mig.apply_migration(self.root, [(7, 3)], dry_run=False, merge=True)
        # 嵌套缺的 IDENTITY 被补入；嵌套已有的 AGENT 不被覆盖；扁平被删
        self.assertEqual((nested / "IDENTITY.md").read_text(), "id")
        self.assertEqual((nested / "AGENT.md").read_text(), "live-agent")
        self.assertFalse((self.root / "bot_7").exists())
        ok, _ = mig.verify(self.root, [(7, 3)])
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
