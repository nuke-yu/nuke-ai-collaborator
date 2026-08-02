"""tests/test_group_db_ready.py — #6/#8: ensure_group_db_ready 路由层依赖 + ready 缓存。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

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


class _CursorContext:
    def __init__(self, row):
        self.cursor = AsyncMock()
        self.cursor.fetchone.return_value = row

    async def __aenter__(self):
        return self.cursor

    async def __aexit__(self, *_args):
        return None


class _Connection:
    def __init__(self, row):
        self.row = row
        self.execute = MagicMock(side_effect=lambda *_args: _CursorContext(self.row))


class _ConnectionContext:
    def __init__(self, row):
        self.connection = _Connection(row)

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return None


class TestRequireGroupMemberReady(unittest.IsolatedAsyncioTestCase):
    async def test_authorizes_before_initializing_group_db(self):
        from api.deps import require_group_member, require_group_member_ready
        from runtime.dbpaths import group_db_path

        with patch("api.deps.global_db", return_value=_ConnectionContext((1,))), \
             patch("api.deps.ensure_group_db_ready", new_callable=AsyncMock) as ensure:
            verified = await require_group_member(group_id=7, user={"uid": 42})
            user = await require_group_member_ready(group_id=7, user=verified)
        self.assertEqual(user["uid"], 42)
        ensure.assert_awaited_once_with(group_db_path(7))

    async def test_non_member_is_hidden_and_group_db_is_not_opened(self):
        from api.deps import require_group_member

        with patch("api.deps.global_db", return_value=_ConnectionContext(None)), \
             patch("api.deps.ensure_group_db_ready", new_callable=AsyncMock) as ensure:
            with self.assertRaises(HTTPException) as caught:
                await require_group_member(group_id=7, user={"uid": 42})
        self.assertEqual(caught.exception.status_code, 404)
        ensure.assert_not_awaited()


class TestIsMissingSchemaError(unittest.TestCase):
    def test_classifies_schema_errors_only(self):
        import sqlite3
        from db.errors import is_missing_schema_error
        self.assertTrue(is_missing_schema_error(sqlite3.OperationalError("no such column: last_recap_ack_id")))
        self.assertTrue(is_missing_schema_error(sqlite3.OperationalError("no such table: reflection_state")))
        self.assertFalse(is_missing_schema_error(sqlite3.OperationalError("database is locked")))
        self.assertFalse(is_missing_schema_error(ValueError("no such column")))


if __name__ == "__main__":
    unittest.main()
