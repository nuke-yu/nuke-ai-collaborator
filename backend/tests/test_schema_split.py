"""CELL-05: central + per-group schema split.

Verifies the data-domain partition (V3 §3): init_central_db creates only central
tables, init_group_db creates only group tables; cross-domain FKs are dropped in
group tables (so a group DB can stand alone) while within-domain FKs are kept and
enforced.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from db.schema_split import CENTRAL_TABLES, GROUP_TABLES
from db.migrations import MIGRATIONS
from memory.infrastructure.schema import MEMORY_GROUP_TABLES


async def _tables(path):
    async with db.connect(path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return {r[0] for r in await cur.fetchall()}


async def _fk_targets(path, table):
    async with db.connect(path) as conn:
        cur = await conn.execute(f"PRAGMA foreign_key_list({table})")
        return {r[2] for r in await cur.fetchall()}  # r[2] = referenced table


class TestSchemaSplit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.central = tempfile.mktemp(suffix="_central.db")
        self.group = tempfile.mktemp(suffix="_group.db")
        await db.init_central_db(self.central)
        await db.init_group_db(self.group)

    async def asyncTearDown(self):
        await db.aclose_writer()
        for p in (self.central, self.group):
            for s in ("", "-wal", "-shm"):
                try:
                    os.unlink(p + s)
                except FileNotFoundError:
                    pass

    async def test_central_has_only_central_tables(self):
        tables = await _tables(self.central)
        self.assertTrue(CENTRAL_TABLES <= tables, CENTRAL_TABLES - tables)
        self.assertFalse(GROUP_TABLES & tables, GROUP_TABLES & tables)
        self.assertIn("agent_tasks", tables)
        self.assertIn("agent_task_requests", tables)
        self.assertIn("agent_task_retry_claims", tables)

    async def test_group_has_only_group_tables(self):
        tables = await _tables(self.group)
        self.assertTrue(GROUP_TABLES <= tables, GROUP_TABLES - tables)
        self.assertFalse(CENTRAL_TABLES & tables, CENTRAL_TABLES & tables)
        self.assertTrue(MEMORY_GROUP_TABLES <= GROUP_TABLES)

    async def test_cross_domain_fks_dropped_in_group(self):
        # messages no longer references central groups/members
        self.assertEqual(await _fk_targets(self.group, "messages"), set())
        self.assertEqual(await _fk_targets(self.group, "agent_sessions"), set())
        self.assertEqual(await _fk_targets(self.group, "workflow_state"), set())
        self.assertEqual(await _fk_targets(self.group, "group_locks"), set())

    async def test_within_domain_fks_kept(self):
        # group-internal FKs survive
        self.assertEqual(await _fk_targets(self.group, "message_reactions"), {"messages"})
        self.assertEqual(await _fk_targets(self.group, "session_events"), {"agent_sessions"})
        self.assertEqual(
            await _fk_targets(self.group, "session_evidence_links"),
            {"agent_sessions", "session_events"},
        )
        # central-internal FK survives
        self.assertEqual(await _fk_targets(self.central, "permission_rules"), {"members"})

    async def test_group_db_inserts_message_without_central_members(self):
        # The whole point: a group DB stands alone — a message with an arbitrary
        # member_id inserts fine (no cross-domain FK), even with foreign_keys=ON.
        async with db.write_connect(self.group) as conn:
            await conn.execute(
                "INSERT INTO messages (group_id, member_id, content) VALUES (1, 999, 'hi')"
            )
            await conn.commit()
            cur = await conn.execute("SELECT content FROM messages WHERE member_id=999")
            self.assertEqual((await cur.fetchone())[0], "hi")

    async def test_within_group_fk_still_enforced(self):
        # a reaction referencing a non-existent message must be rejected (FK on)
        import aiosqlite
        async with db.write_connect(self.group) as conn:
            with self.assertRaises(aiosqlite.IntegrityError):
                await conn.execute(
                    "INSERT INTO message_reactions (message_id, member_id, emoji) "
                    "VALUES (123456, 1, '👍')"
                )
                await conn.commit()

    async def test_fresh_db_stamped_at_current_version(self):
        async with db.connect(self.central) as conn:
            cur = await conn.execute("SELECT MAX(version) FROM _schema_version")
            self.assertEqual((await cur.fetchone())[0], len(MIGRATIONS))

    async def test_init_central_db_catches_up_legacy_db(self):
        """回归：分库前的老单库被改用作中央库时，带着一张缺新列的 messages 表、
        _schema_version 停在旧版本。init_central_db 必须像 group/legacy 路径一样跑
        run_migrations 把它补齐——否则 HTTP 读消息端点查中央库会 no such column: m.meta。"""
        legacy = tempfile.mktemp(suffix="_legacy_central.db")
        try:
            async with db.connect(legacy) as conn:
                await conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
                await conn.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL, "
                                   "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                # 停在 meta(最后一个迁移)之前一版，模拟 db/chat.db 的 v15 状态
                await conn.execute("INSERT INTO _schema_version (version) VALUES (?)",
                                   (15,))
                await conn.commit()
            await db.init_central_db(legacy)
            async with db.connect(legacy) as conn:
                cur = await conn.execute("PRAGMA table_info(messages)")
                cols = {r[1] for r in await cur.fetchall()}
            self.assertIn("meta", cols)
        finally:
            await db.aclose_writer()
            for s in ("", "-wal", "-shm"):
                try:
                    os.unlink(legacy + s)
                except FileNotFoundError:
                    pass

    async def test_templates_seeded_in_central(self):
        async with db.connect(self.central) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM role_templates")
            self.assertGreater((await cur.fetchone())[0], 0)


if __name__ == "__main__":
    unittest.main()
