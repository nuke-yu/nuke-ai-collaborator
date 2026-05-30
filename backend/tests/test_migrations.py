"""
tests/test_migrations.py — db/migrations.py 单元测试

覆盖：
  - run_migrations: _schema_version 表创建、首次迁移、幂等性、增量迁移
  - migration_001: 旧 schema DB 补列、新 schema DB 幂等
  - init_db 集成: 新装 DB、存量 DB 迁移、连续调用幂等
"""
import asyncio
import os
import sys
import tempfile
import unittest

import aiosqlite

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
from db.migrations import (
    run_migrations, migration_001, migration_002, MIGRATIONS, _safe_add_column,
)
from db.schema import init_db


# ─── helper ──────────────────────────────────────────────────────────────────

async def _get_version(conn) -> int | None:
    cur = await conn.execute("SELECT MAX(version) FROM _schema_version")
    row = await cur.fetchone()
    return row[0]


async def _table_columns(conn, table: str) -> list[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in await cur.fetchall()]


async def _has_table(conn, table: str) -> bool:
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return await cur.fetchone() is not None


# ─── base class with temp-DB per test ────────────────────────────────────────

class MigrationTestCase(unittest.IsolatedAsyncioTestCase):
    """Creates a fresh temp DB for every test method."""

    async def asyncSetUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self._db_path)  # start empty

    async def asyncTearDown(self):
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    def _connect(self):
        return aiosqlite.connect(self._db_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 一、run_migrations 核心逻辑
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunMigrations(MigrationTestCase):

    async def _seed_base_schema(self, conn):
        """生产流程下 init_db 先建基础表再迁移（schema.py）；这里同样先建好
        基础表，避免迁移因 'no such table' 而（按 DFT-038 的新语义）上抛。"""
        await conn.executescript(_OLD_SCHEMA)
        await conn.commit()

    async def test_creates_schema_version_table(self):
        """run_migrations 在已建基础表的 DB 上创建 _schema_version 表。"""
        async with self._connect() as conn:
            await self._seed_base_schema(conn)
            self.assertFalse(await _has_table(conn, "_schema_version"))
            await run_migrations(conn)
            self.assertTrue(await _has_table(conn, "_schema_version"))

    async def test_fresh_db_reaches_current_version(self):
        """基础表就绪后执行，版本号等于 MIGRATIONS 长度。"""
        async with self._connect() as conn:
            await self._seed_base_schema(conn)
            await run_migrations(conn)
            self.assertEqual(await _get_version(conn), len(MIGRATIONS))

    async def test_idempotent_on_repeat_call(self):
        """连续两次调用不产生重复版本行，版本号不变。"""
        async with self._connect() as conn:
            await self._seed_base_schema(conn)
            await run_migrations(conn)
            await run_migrations(conn)
            cur = await conn.execute("SELECT COUNT(*) FROM _schema_version")
            count = (await cur.fetchone())[0]
            # 每个 migration 写一行，重复调用不应增加
            self.assertEqual(count, len(MIGRATIONS))

    async def test_partial_state_runs_only_pending(self):
        """已在版本 N，只执行 N+1 之后的迁移。"""
        call_log = []

        async def fake_m1(db):
            call_log.append(1)

        async def fake_m2(db):
            call_log.append(2)

        async with self._connect() as conn:
            # 建表 + 手动设为版本 1（表示 m1 已完成）
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS _schema_version (
                    version INTEGER NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("INSERT INTO _schema_version (version) VALUES (1)")
            await conn.commit()

            import db.migrations as _mig
            orig = list(_mig.MIGRATIONS)
            _mig.MIGRATIONS[:] = [fake_m1, fake_m2]
            try:
                await run_migrations(conn)
            finally:
                _mig.MIGRATIONS[:] = orig

        self.assertEqual(call_log, [2], "只应执行 migration_002，跳过 migration_001")

    async def test_version_advances_after_each_migration(self):
        """每个迁移执行后立即写入版本号（逐步推进）。"""
        applied = []

        async def recorder(db):
            cur = await db.execute("SELECT MAX(version) FROM _schema_version")
            row = await cur.fetchone()
            applied.append(row[0] or 0)  # 此时尚未写入本次版本

        async with self._connect() as conn:
            import db.migrations as _mig
            orig = list(_mig.MIGRATIONS)
            _mig.MIGRATIONS[:] = [recorder, recorder]
            try:
                await run_migrations(conn)
            finally:
                _mig.MIGRATIONS[:] = orig

            final = await _get_version(conn)

        self.assertEqual(final, 2)
        # 第一次执行时 MAX(version) 为 0，第二次为 1
        self.assertEqual(applied, [0, 1])

    async def test_no_op_when_already_at_latest(self):
        """已是最新版本时，不执行任何迁移函数。"""
        called = []

        async def spy(db):
            called.append(True)

        async with self._connect() as conn:
            import db.migrations as _mig
            orig = list(_mig.MIGRATIONS)
            _mig.MIGRATIONS[:] = [spy]
            try:
                await run_migrations(conn)   # 首次：执行 spy，version=1
                called.clear()
                await run_migrations(conn)   # 再次：不应再调用 spy
            finally:
                _mig.MIGRATIONS[:] = orig

        self.assertEqual(called, [], "已最新版本时不应执行任何迁移")


# ═══════════════════════════════════════════════════════════════════════════════
# 二、migration_001 — 旧 schema 补列
# ═══════════════════════════════════════════════════════════════════════════════

# 初始 schema（模拟未含扩展列的旧版本）
_OLD_SCHEMA = """
    CREATE TABLE groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        role TEXT,
        system_prompt TEXT,
        avatar_color TEXT DEFAULT '#6366f1'
    );
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        member_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE role_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        summary TEXT NOT NULL,
        covered_through_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
"""

_EXPECTED_MEMBERS_COLS = {
    "model_provider", "model_name", "auto_reply", "context_cleared_at",
    "temperature", "max_tokens", "personality_prompt", "executor_id",
    "executor_config", "done_keyword",
}

_EXPECTED_MESSAGES_COLS = {
    "reply_to_id", "edited_at", "is_deleted",
    "file_url", "file_name", "file_size", "file_type", "is_auto_reply",
}


class TestMigration001(MigrationTestCase):

    async def _make_old_db(self):
        """Populate the temp DB with old schema then close; callers open their own conn."""
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.executescript(_OLD_SCHEMA)
            await conn.commit()

    async def test_adds_members_columns_to_old_db(self):
        """migration_001 在旧 schema DB 上补全 members 扩展列。"""
        await self._make_old_db()
        async with self._connect() as conn:
            await migration_001(conn)
            cols = set(await _table_columns(conn, "members"))
        self.assertTrue(
            _EXPECTED_MEMBERS_COLS.issubset(cols),
            f"缺少列: {_EXPECTED_MEMBERS_COLS - cols}",
        )

    async def test_adds_messages_columns_to_old_db(self):
        """migration_001 在旧 schema DB 上补全 messages 扩展列。"""
        await self._make_old_db()
        async with self._connect() as conn:
            await migration_001(conn)
            cols = set(await _table_columns(conn, "messages"))
        self.assertTrue(
            _EXPECTED_MESSAGES_COLS.issubset(cols),
            f"缺少列: {_EXPECTED_MESSAGES_COLS - cols}",
        )

    async def test_adds_announcement_to_groups(self):
        """migration_001 为 groups 表添加 announcement 列。"""
        await self._make_old_db()
        async with self._connect() as conn:
            await migration_001(conn)
            cols = await _table_columns(conn, "groups")
        self.assertIn("announcement", cols)

    async def test_adds_bot_id_to_role_summaries(self):
        """migration_001 为 role_summaries 表添加 bot_id 列。"""
        await self._make_old_db()
        async with self._connect() as conn:
            await migration_001(conn)
            cols = await _table_columns(conn, "role_summaries")
        self.assertIn("bot_id", cols)

    async def test_idempotent_on_new_schema_db(self):
        """migration_001 在列已存在时静默跳过，不抛异常。"""
        orig_path = database.DB_PATH
        database.DB_PATH = self._db_path
        try:
            await init_db()
            async with self._connect() as conn:
                await migration_001(conn)  # should not raise
        finally:
            database.DB_PATH = orig_path

    async def test_idempotent_on_old_db_repeat_call(self):
        """migration_001 多次执行不报错。"""
        await self._make_old_db()
        async with self._connect() as conn:
            await migration_001(conn)
            await migration_001(conn)  # second call must not raise


# ═══════════════════════════════════════════════════════════════════════════════
# 三、init_db 集成测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestInitDbIntegration(MigrationTestCase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._orig_path = database.DB_PATH
        database.DB_PATH = self._db_path

    async def asyncTearDown(self):
        database.DB_PATH = self._orig_path
        await super().asyncTearDown()

    async def test_new_db_has_schema_version(self):
        """新装 DB 启动后 _schema_version 存在且版本正确。"""
        await init_db()
        async with self._connect() as conn:
            self.assertTrue(await _has_table(conn, "_schema_version"))
            self.assertEqual(await _get_version(conn), len(MIGRATIONS))

    async def test_new_db_has_all_tables(self):
        """新装 DB 包含所有业务表。"""
        await init_db()
        expected = {
            "groups", "members", "messages", "role_summaries",
            "message_embeddings", "member_read", "message_reactions",
            "pinned_messages", "role_templates", "permission_rules",
        }
        async with self._connect() as conn:
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in await cur.fetchall()}
        self.assertTrue(expected.issubset(tables), f"缺少表: {expected - tables}")

    async def test_new_db_members_has_all_columns(self):
        """新装 DB 的 members 表包含所有扩展列。"""
        await init_db()
        async with self._connect() as conn:
            cols = set(await _table_columns(conn, "members"))
        self.assertTrue(_EXPECTED_MEMBERS_COLS.issubset(cols))

    async def test_new_db_messages_has_all_columns(self):
        """新装 DB 的 messages 表包含所有扩展列。"""
        await init_db()
        async with self._connect() as conn:
            cols = set(await _table_columns(conn, "messages"))
        self.assertTrue(_EXPECTED_MESSAGES_COLS.issubset(cols))

    async def test_init_db_idempotent(self):
        """连续两次 init_db 不报错，版本号不重复累加。"""
        await init_db()
        await init_db()
        async with self._connect() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM _schema_version")
            count = (await cur.fetchone())[0]
        self.assertEqual(count, len(MIGRATIONS))

    async def test_old_db_migrated_on_init(self):
        """旧 schema DB（无 _schema_version）在 init_db 后被迁移到最新版本。"""
        # 先建一个旧 DB（只有基础表，无扩展列）
        async with self._connect() as conn:
            await conn.executescript(_OLD_SCHEMA)
            await conn.commit()

        # 运行 init_db（会触发 run_migrations）
        await init_db()

        async with self._connect() as conn:
            self.assertEqual(await _get_version(conn), len(MIGRATIONS))
            cols = set(await _table_columns(conn, "members"))
        self.assertTrue(_EXPECTED_MEMBERS_COLS.issubset(cols))

    async def test_seeds_default_templates(self):
        """init_db 写入默认角色模板。"""
        await init_db()
        async with self._connect() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM role_templates")
            count = (await cur.fetchone())[0]
        self.assertGreater(count, 0, "应有默认角色模板")

    async def test_new_db_messages_has_token_columns(self):
        """新装 DB 的 messages 表包含 input_tokens 和 output_tokens 列（migration_002）。"""
        await init_db()
        async with self._connect() as conn:
            cols = set(await _table_columns(conn, "messages"))
        self.assertIn("input_tokens", cols)
        self.assertIn("output_tokens", cols)


# ═══════════════════════════════════════════════════════════════════════════════
# 四、migration_002 — token 列
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigration002(MigrationTestCase):

    async def _make_pre_002_db(self):
        """建一个包含 migration_001 列、但缺少 migration_002 列的 DB。"""
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.executescript(_OLD_SCHEMA)
            await migration_001(conn)
            await conn.commit()

    async def test_adds_input_tokens_column(self):
        """migration_002 为 messages 表添加 input_tokens 列。"""
        await self._make_pre_002_db()
        async with self._connect() as conn:
            await migration_002(conn)
            cols = await _table_columns(conn, "messages")
        self.assertIn("input_tokens", cols)

    async def test_adds_output_tokens_column(self):
        """migration_002 为 messages 表添加 output_tokens 列。"""
        await self._make_pre_002_db()
        async with self._connect() as conn:
            await migration_002(conn)
            cols = await _table_columns(conn, "messages")
        self.assertIn("output_tokens", cols)

    async def test_token_columns_default_null(self):
        """新插入的消息行，token 列默认值为 NULL。"""
        orig_path = database.DB_PATH
        database.DB_PATH = self._db_path
        try:
            await init_db()
            async with self._connect() as conn:
                await conn.execute(
                    "INSERT INTO groups (name) VALUES ('g')"
                )
                await conn.execute(
                    "INSERT INTO members (group_id, name, type) VALUES (1, 'u', 'human')"
                )
                await conn.execute(
                    "INSERT INTO messages (group_id, member_id, content) VALUES (1, 1, 'hi')"
                )
                await conn.commit()
                cur = await conn.execute(
                    "SELECT input_tokens, output_tokens FROM messages LIMIT 1"
                )
                row = await cur.fetchone()
        finally:
            database.DB_PATH = orig_path
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])

    async def test_idempotent_on_new_schema_db(self):
        """migration_002 在已有列时静默跳过，不抛异常。"""
        orig_path = database.DB_PATH
        database.DB_PATH = self._db_path
        try:
            await init_db()
            async with self._connect() as conn:
                await migration_002(conn)  # second run must not raise
        finally:
            database.DB_PATH = orig_path

    async def test_idempotent_on_old_db_repeat_call(self):
        """migration_002 在旧 DB 上多次执行不报错。"""
        await self._make_pre_002_db()
        async with self._connect() as conn:
            await migration_002(conn)
            await migration_002(conn)


# ═══════════════════════════════════════════════════════════════════════════════
# 五、DFT-038 — 迁移错误传播（不再吞 lock/磁盘满/语法错误并谎报成功）
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigrationErrorPropagation(MigrationTestCase):

    async def test_safe_add_column_swallows_duplicate(self):
        """重复列是良性情况，_safe_add_column 静默跳过。"""
        async with self._connect() as conn:
            await conn.execute("CREATE TABLE t (a INTEGER)")
            # 不抛异常即通过
            await _safe_add_column(conn, "ALTER TABLE t ADD COLUMN a INTEGER")

    async def test_safe_add_column_reraises_non_duplicate(self):
        """非'duplicate column'的 OperationalError（如表不存在）必须上抛，不被吞。"""
        async with self._connect() as conn:
            with self.assertRaises(aiosqlite.OperationalError):
                await _safe_add_column(
                    conn, "ALTER TABLE nonexistent_table ADD COLUMN x INTEGER"
                )

    async def test_failed_migration_does_not_advance_version(self):
        """迁移内真实错误上抛后，_schema_version 不前进 → 下次启动会重试，不谎报成功。"""
        async def boom(db):
            # 表不存在 → OperationalError，非 duplicate column
            await _safe_add_column(db, "ALTER TABLE does_not_exist ADD COLUMN x INTEGER")

        async with self._connect() as conn:
            import db.migrations as _mig
            orig = list(_mig.MIGRATIONS)
            _mig.MIGRATIONS[:] = [boom]
            try:
                with self.assertRaises(aiosqlite.OperationalError):
                    await run_migrations(conn)
                self.assertIsNone(
                    await _get_version(conn),
                    "失败的迁移不应被记入 _schema_version（表内应无版本行）",
                )
            finally:
                _mig.MIGRATIONS[:] = orig


if __name__ == "__main__":
    unittest.main()
