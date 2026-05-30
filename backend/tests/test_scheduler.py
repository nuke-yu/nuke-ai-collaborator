"""
tests/test_scheduler.py — 定时任务模块单元测试

覆盖：
  1. migration_003 — cron_jobs 表创建
  2. store.py — CRUD（create / get / list / update / toggle / delete）
  3. engine.py — validate_cron_expr, reload_job, run_now, start/stop
  4. runner.py — fire_job（mock dispatch_bots）
  5. router.py — API 端点（E2E via httpx AsyncClient）
"""
import os
import sys
import asyncio
import shutil
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
import db.migrations as _migrations_mod
from db.schema import init_db

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_scheduler.db")
_GROUP_ID = 1
_BOT_ID   = 2


def _use_test_db():
    _db_mod.DB_PATH = _TEST_DB


def _restore_db(orig):
    _db_mod.DB_PATH = orig


# ─── helpers ─────────────────────────────────────────────────────────────────

async def _seed_db():
    import aiosqlite
    async with aiosqlite.connect(_TEST_DB) as conn:
        await conn.execute(f"INSERT OR IGNORE INTO groups (id, name) VALUES ({_GROUP_ID}, 'G')")
        await conn.execute(
            f"INSERT OR IGNORE INTO members (id, group_id, name, type) "
            f"VALUES ({_BOT_ID}, {_GROUP_ID}, 'Bot', 'bot')"
        )
        await conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Migration
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigration003(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await init_db()

    async def asyncTearDown(self):
        _restore_db(self._orig)
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_cron_jobs_table_exists(self):
        import aiosqlite
        async with aiosqlite.connect(_TEST_DB) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cron_jobs'"
            ) as cur:
                row = await cur.fetchone()
        self.assertIsNotNone(row, "cron_jobs table should exist after migration_003")

    async def test_cron_jobs_columns(self):
        import aiosqlite
        async with aiosqlite.connect(_TEST_DB) as conn:
            async with conn.execute("PRAGMA table_info(cron_jobs)") as cur:
                cols = {row[1] for row in await cur.fetchall()}
        expected = {"id", "bot_id", "group_id", "cron_expr", "message", "label", "enabled", "created_at"}
        self.assertTrue(expected.issubset(cols))

    async def test_idempotent(self):
        from db.migrations import migration_003
        import aiosqlite
        async with aiosqlite.connect(_TEST_DB) as conn:
            await migration_003(conn)   # second call must not raise


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Store
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulerStore(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await init_db()
        await _seed_db()

    async def asyncTearDown(self):
        _restore_db(self._orig)
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def _create(self, **kw):
        from scheduler.store import create_job
        defaults = dict(bot_id=_BOT_ID, group_id=_GROUP_ID,
                        cron_expr="0 9 * * 1-5", message="日报", label="morning")
        return await create_job(**{**defaults, **kw})

    async def test_create_returns_job_with_id(self):
        job = await self._create()
        self.assertIn("id", job)
        self.assertEqual(job["cron_expr"], "0 9 * * 1-5")
        self.assertEqual(job["message"], "日报")
        self.assertTrue(job["enabled"])

    async def test_get_job_returns_correct_record(self):
        from scheduler.store import get_job
        job = await self._create()
        fetched = await get_job(job["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], job["id"])

    async def test_get_nonexistent_returns_none(self):
        from scheduler.store import get_job
        self.assertIsNone(await get_job(999))

    async def test_list_all(self):
        from scheduler.store import list_jobs
        await self._create(label="a")
        await self._create(label="b")
        jobs = await list_jobs()
        self.assertGreaterEqual(len(jobs), 2)

    async def test_list_enabled_only(self):
        from scheduler.store import list_jobs
        j1 = await self._create(enabled=True)
        j2 = await self._create(enabled=False)
        enabled = await list_jobs(enabled_only=True)
        ids = [j["id"] for j in enabled]
        self.assertIn(j1["id"], ids)
        self.assertNotIn(j2["id"], ids)

    async def test_list_filter_by_bot(self):
        from scheduler.store import list_jobs
        j = await self._create()
        result = await list_jobs(bot_id=_BOT_ID)
        self.assertTrue(any(r["id"] == j["id"] for r in result))
        result_none = await list_jobs(bot_id=999)
        self.assertFalse(any(r["id"] == j["id"] for r in result_none))

    async def test_update_changes_fields(self):
        from scheduler.store import update_job
        job = await self._create()
        updated = await update_job(job["id"], message="新消息", label="new")
        self.assertEqual(updated["message"], "新消息")
        self.assertEqual(updated["label"], "new")
        self.assertEqual(updated["cron_expr"], job["cron_expr"])  # unchanged

    async def test_update_ignores_unknown_fields(self):
        from scheduler.store import update_job
        job = await self._create()
        updated = await update_job(job["id"], evil="drop table")
        self.assertNotIn("evil", updated)

    async def test_toggle_flips_enabled(self):
        from scheduler.store import toggle_job
        job = await self._create(enabled=True)
        toggled = await toggle_job(job["id"])
        self.assertFalse(toggled["enabled"])
        toggled2 = await toggle_job(job["id"])
        self.assertTrue(toggled2["enabled"])

    async def test_delete_removes_record(self):
        from scheduler.store import delete_job, get_job
        job = await self._create()
        await delete_job(job["id"])
        self.assertIsNone(await get_job(job["id"]))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Engine
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulerEngine(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await init_db()
        await _seed_db()

    async def asyncTearDown(self):
        import scheduler.engine as eng
        eng.stop()
        _restore_db(self._orig)
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    # --- validate_cron_expr ---

    def test_valid_cron_exprs(self):
        from scheduler.engine import validate_cron_expr
        valid = ["0 9 * * 1-5", "*/30 * * * *", "0 0 1 * *", "0 * * * *"]
        for expr in valid:
            with self.subTest(expr=expr):
                self.assertTrue(validate_cron_expr(expr))

    def test_invalid_cron_exprs(self):
        from scheduler.engine import validate_cron_expr
        invalid = ["", "bad", "* * * *", "99 * * * *", "0 25 * * *"]
        for expr in invalid:
            with self.subTest(expr=expr):
                self.assertFalse(validate_cron_expr(expr))

    # --- timezone (DFT-040) ---

    def test_parse_cron_interpreted_in_utc(self):
        """_parse_cron 生成的 CronTrigger 以 UTC 解释，不随宿主本地时区/DST 平移。"""
        from scheduler.engine import _parse_cron
        trigger = _parse_cron("0 9 * * *")
        self.assertIsNotNone(trigger)
        self.assertEqual(str(trigger.timezone), "UTC")

    async def test_scheduler_runs_in_utc(self):
        """AsyncIOScheduler 以 UTC 时区运行。"""
        import scheduler.engine as eng
        await eng.start()
        self.assertEqual(str(eng._scheduler.timezone), "UTC")

    # --- start / stop ---

    async def test_start_creates_running_scheduler(self):
        import scheduler.engine as eng
        await eng.start()
        self.assertIsNotNone(eng._scheduler)
        self.assertTrue(eng._scheduler.running)

    async def test_stop_shuts_down(self):
        import scheduler.engine as eng
        await eng.start()
        eng.stop()
        self.assertIsNone(eng._scheduler)

    async def test_start_loads_enabled_jobs(self):
        from scheduler.store import create_job
        import scheduler.engine as eng
        j = await create_job(_BOT_ID, _GROUP_ID, "0 9 * * *", "hi", enabled=True)
        await eng.start()
        self.assertIsNotNone(eng._scheduler.get_job(eng._apscheduler_job_id(j["id"])))

    async def test_start_skips_disabled_jobs(self):
        from scheduler.store import create_job
        import scheduler.engine as eng
        j = await create_job(_BOT_ID, _GROUP_ID, "0 9 * * *", "hi", enabled=False)
        await eng.start()
        self.assertIsNone(eng._scheduler.get_job(eng._apscheduler_job_id(j["id"])))

    # --- reload_job ---

    async def test_reload_adds_enabled_job(self):
        import scheduler.engine as eng
        await eng.start()
        job = {"id": 99, "bot_id": _BOT_ID, "group_id": _GROUP_ID,
               "cron_expr": "0 8 * * *", "message": "test", "enabled": True}
        eng.reload_job(job)
        self.assertIsNotNone(eng._scheduler.get_job(eng._apscheduler_job_id(99)))

    async def test_reload_removes_disabled_job(self):
        import scheduler.engine as eng
        await eng.start()
        job = {"id": 42, "bot_id": _BOT_ID, "group_id": _GROUP_ID,
               "cron_expr": "0 8 * * *", "message": "test", "enabled": True}
        eng.reload_job(job)
        eng.reload_job({**job, "enabled": False})
        self.assertIsNone(eng._scheduler.get_job(eng._apscheduler_job_id(42)))

    async def test_reload_invalid_cron_not_scheduled(self):
        import scheduler.engine as eng
        await eng.start()
        job = {"id": 77, "bot_id": _BOT_ID, "group_id": _GROUP_ID,
               "cron_expr": "bad expr", "message": "x", "enabled": True}
        eng.reload_job(job)   # must not raise
        self.assertIsNone(eng._scheduler.get_job(eng._apscheduler_job_id(77)))

    # --- run_now ---

    async def test_run_now_calls_fire_job(self):
        import scheduler.engine as eng
        job = {"id": 1, "bot_id": _BOT_ID, "group_id": _GROUP_ID, "message": "ping"}
        with patch("scheduler.engine.fire_job", new_callable=AsyncMock) as mock_fire:
            await eng.run_now(job)
        mock_fire.assert_awaited_once_with(_BOT_ID, _GROUP_ID, "ping")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Runner
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulerRunner(unittest.IsolatedAsyncioTestCase):
    """runner.py uses lazy imports inside fire_job, so patches target the source modules."""

    def _make_async_cm(self):
        """Async context manager mock that yields itself."""
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        return mock_conn

    async def test_fire_job_calls_dispatch_bots(self):
        from scheduler.runner import fire_job
        fake_members = [
            {"id": _BOT_ID, "type": "bot", "name": "Bot",
             "system_prompt": "", "role": "", "avatar_color": "#fff",
             "model_provider": "deepseek", "model_name": "deepseek-chat",
             "executor_id": "simple_v1", "executor_config": "{}",
             "temperature": 0.7, "max_tokens": 4096, "done_keyword": None,
             "personality_prompt": None, "context_cleared_at": None,
             "auto_reply": None, "group_id": _GROUP_ID},
        ]
        with patch("db.get_db", return_value=self._make_async_cm()), \
             patch("db.get_members", new_callable=AsyncMock, return_value=fake_members), \
             patch("db.get_messages", new_callable=AsyncMock, return_value=[]), \
             patch("core.orchestrator.dispatch_bots", new_callable=AsyncMock) as mock_dispatch:
            await fire_job(_BOT_ID, _GROUP_ID, "hello scheduler")

        mock_dispatch.assert_awaited_once()
        call_args = mock_dispatch.call_args
        self.assertEqual(call_args.args[0], _GROUP_ID)
        self.assertEqual(call_args.args[2], "hello scheduler")

    async def test_fire_job_skips_unknown_bot(self):
        from scheduler.runner import fire_job
        with patch("db.get_db", return_value=self._make_async_cm()), \
             patch("db.get_members", new_callable=AsyncMock, return_value=[]), \
             patch("db.get_messages", new_callable=AsyncMock, return_value=[]), \
             patch("core.orchestrator.dispatch_bots", new_callable=AsyncMock) as mock_dispatch:
            await fire_job(999, _GROUP_ID, "ping")

        mock_dispatch.assert_not_awaited()

    async def test_fire_job_handles_db_error_gracefully(self):
        from scheduler.runner import fire_job
        with patch("db.get_db", side_effect=Exception("DB down")), \
             patch("core.orchestrator.dispatch_bots", new_callable=AsyncMock) as mock_dispatch:
            await fire_job(_BOT_ID, _GROUP_ID, "fail gracefully")  # must not raise
        mock_dispatch.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Router (E2E via httpx)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulerRouter(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig_db = _db_mod.DB_PATH
        _use_test_db()
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await init_db()
        await _seed_db()
        # Reset engine state (no live scheduler needed for router tests)
        import scheduler.engine as eng
        eng._scheduler = None

    async def asyncTearDown(self):
        import scheduler.engine as eng
        eng.stop()
        _restore_db(self._orig_db)
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def _client(self):
        from httpx import AsyncClient
        from main import app
        return AsyncClient(app=app, base_url="http://test")

    async def test_create_job_returns_200(self):
        async with await self._client() as ac:
            r = await ac.post("/api/cron-jobs", json={
                "bot_id": _BOT_ID, "group_id": _GROUP_ID,
                "cron_expr": "0 9 * * 1-5", "message": "日报",
            })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("id", data)
        self.assertEqual(data["cron_expr"], "0 9 * * 1-5")

    async def test_create_invalid_cron_returns_400(self):
        async with await self._client() as ac:
            r = await ac.post("/api/cron-jobs", json={
                "bot_id": _BOT_ID, "group_id": _GROUP_ID,
                "cron_expr": "not-a-cron", "message": "x",
            })
        self.assertEqual(r.status_code, 400)

    async def test_create_missing_fields_returns_400(self):
        async with await self._client() as ac:
            r = await ac.post("/api/cron-jobs", json={"bot_id": _BOT_ID})
        self.assertEqual(r.status_code, 400)

    async def test_list_jobs(self):
        async with await self._client() as ac:
            await ac.post("/api/cron-jobs", json={
                "bot_id": _BOT_ID, "group_id": _GROUP_ID,
                "cron_expr": "0 8 * * *", "message": "早安",
            })
            r = await ac.get("/api/cron-jobs")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json()["jobs"], list)
        self.assertGreater(len(r.json()["jobs"]), 0)

    async def test_get_nonexistent_job_404(self):
        async with await self._client() as ac:
            r = await ac.get("/api/cron-jobs/99999")
        self.assertEqual(r.status_code, 404)

    async def test_update_job(self):
        async with await self._client() as ac:
            created = (await ac.post("/api/cron-jobs", json={
                "bot_id": _BOT_ID, "group_id": _GROUP_ID,
                "cron_expr": "0 9 * * *", "message": "old",
            })).json()
            r = await ac.put(f"/api/cron-jobs/{created['id']}", json={"message": "new"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["message"], "new")

    async def test_update_invalid_cron_returns_400(self):
        async with await self._client() as ac:
            created = (await ac.post("/api/cron-jobs", json={
                "bot_id": _BOT_ID, "group_id": _GROUP_ID,
                "cron_expr": "0 9 * * *", "message": "x",
            })).json()
            r = await ac.put(f"/api/cron-jobs/{created['id']}", json={"cron_expr": "bad"})
        self.assertEqual(r.status_code, 400)

    async def test_toggle_job(self):
        async with await self._client() as ac:
            created = (await ac.post("/api/cron-jobs", json={
                "bot_id": _BOT_ID, "group_id": _GROUP_ID,
                "cron_expr": "0 9 * * *", "message": "x", "enabled": True,
            })).json()
            r = await ac.post(f"/api/cron-jobs/{created['id']}/toggle")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])

    async def test_delete_job(self):
        async with await self._client() as ac:
            created = (await ac.post("/api/cron-jobs", json={
                "bot_id": _BOT_ID, "group_id": _GROUP_ID,
                "cron_expr": "0 9 * * *", "message": "bye",
            })).json()
            r = await ac.delete(f"/api/cron-jobs/{created['id']}")
            self.assertEqual(r.status_code, 200)
            r2 = await ac.get(f"/api/cron-jobs/{created['id']}")
        self.assertEqual(r2.status_code, 404)

    async def test_run_now(self):
        async with await self._client() as ac:
            created = (await ac.post("/api/cron-jobs", json={
                "bot_id": _BOT_ID, "group_id": _GROUP_ID,
                "cron_expr": "0 9 * * *", "message": "ping",
            })).json()
            with patch("scheduler.engine.fire_job", new_callable=AsyncMock) as mock_fire:
                r = await ac.post(f"/api/cron-jobs/{created['id']}/run")
        self.assertEqual(r.status_code, 200)
        mock_fire.assert_awaited_once_with(_BOT_ID, _GROUP_ID, "ping")


if __name__ == "__main__":
    unittest.main()
