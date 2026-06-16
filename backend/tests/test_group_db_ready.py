"""tests/test_group_db_ready.py — #6/#8: ensure_group_db_ready 路由层依赖 + ready 缓存。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEnsureGroupDbReadyCache(unittest.IsolatedAsyncioTestCase):
    async def test_caches_ready_path_skips_repeat_migrate(self):
        """同一群库第二次 ensure 命中进程内缓存，不再重跑 init/migrate（#8 perf）。"""
        from db.schema_split import ensure_group_db_ready
        import db.schema_split as ss
        ss._ready_group_dbs.clear()
        d = tempfile.mkdtemp()
        path = os.path.join(d, "chat.db")
        with patch("db.migrations.run_migrations", new_callable=AsyncMock) as mock_mig:
            await ensure_group_db_ready(path)
            await ensure_group_db_ready(path)
        self.assertEqual(mock_mig.await_count, 1)  # 第二次命中缓存
        self.assertIn(path, ss._ready_group_dbs)


class TestEnsureGroupReadyDependency(unittest.IsolatedAsyncioTestCase):
    async def test_ensures_group_db_for_given_group_id(self):
        from api.deps import ensure_group_ready
        from runtime.dbpaths import group_db_path
        with patch("api.deps.ensure_group_db_ready", new_callable=AsyncMock) as mock_ensure:
            await ensure_group_ready(group_id=7)
        mock_ensure.assert_awaited_once_with(group_db_path(7))

    async def test_noop_when_group_id_missing(self):
        """group_id 为 None（如 sessions 的可选 query 参数缺省）→ 不做任何事，不报错。"""
        from api.deps import ensure_group_ready
        with patch("api.deps.ensure_group_db_ready", new_callable=AsyncMock) as mock_ensure:
            await ensure_group_ready(group_id=None)
        mock_ensure.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
