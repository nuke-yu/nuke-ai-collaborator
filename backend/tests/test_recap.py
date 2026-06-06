import sys
import os
import shutil
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
from core.recap import generate_and_cache_recap, clear_recap
from main import app
from httpx import AsyncClient

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_recap_chat.db")

class TestAwaySummaryRecap(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Clean up database
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

        database.DB_PATH = TEST_DB_PATH

        # Reset module-level state for recap generator to prevent test leakage
        from core.recap.generator import _last_generated, _generating_groups
        _last_generated.clear()
        _generating_groups.clear()

        # Set up schema and run migrations
        await database.init_db()

        # Patch bus broadcast and others
        self.patcher_bus = patch("bus.bus.broadcast", new_callable=AsyncMock)
        self.mock_broadcast = self.patcher_bus.start()

        # Seed test group and messages
        async with database.get_db() as db_conn:
            await db_conn.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db_conn.execute(
                "INSERT INTO members (id, group_id, name, type, role, model_provider, model_name) "
                "VALUES (10, 1, 'DevBot', 'bot', 'Developer', 'deepseek', 'deepseek-chat')"
            )
            await db_conn.execute(
                "INSERT INTO messages (id, group_id, member_id, content, sender_name, sender_type) "
                "VALUES (100, 1, 10, 'Hello from bot', 'DevBot', 'bot')"
            )
            await db_conn.commit()

    async def asyncTearDown(self):
        self.patcher_bus.stop()
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_generate_and_cache_recap_success(self, mock_call_ai):
        mock_call_ai.return_value = {"content": "This is a recap summary."}

        summary = await generate_and_cache_recap(1)

        self.assertEqual(summary, "This is a recap summary.")
        mock_call_ai.assert_called_once()
        self.mock_broadcast.assert_called_with(1, {
            "type": "recap_updated",
            "group_id": 1,
            "away_summary": "This is a recap summary."
        })

        # Verify cached value in db
        async with database.get_db() as db_conn:
            group = await database.get_group(db_conn, 1)
            self.assertEqual(group["away_summary"], "This is a recap summary.")

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_generate_and_cache_recap_empty_messages(self, mock_call_ai):
        # Delete messages first
        async with database.get_db() as db_conn:
            await db_conn.execute("DELETE FROM messages WHERE group_id = 1")
            await db_conn.commit()

        summary = await generate_and_cache_recap(1)
        self.assertIsNone(summary)
        mock_call_ai.assert_not_called()

    async def test_clear_recap(self):
        # Set recap first
        async with database.get_db() as db_conn:
            await db_conn.execute("UPDATE groups SET away_summary = 'Old Recap' WHERE id = 1")
            await db_conn.commit()

        await clear_recap(1)

        # Verify cleared in db
        async with database.get_db() as db_conn:
            group = await database.get_group(db_conn, 1)
            self.assertIsNone(group["away_summary"])

        self.mock_broadcast.assert_called_with(1, {
            "type": "recap_updated",
            "group_id": 1,
            "away_summary": None
        })

    def test_schema_consistency_groups_and_messages(self):
        """Verify that the groups table in CENTRAL_DDL has away_summary column,
        and messages table in GROUP_DDL has meta and other expected columns."""
        from db.schema_split import _CENTRAL_DDL, _GROUP_DDL
        
        # Check central DDL groups table contains away_summary
        groups_ddl = next((ddl for ddl in _CENTRAL_DDL if "CREATE TABLE" in ddl and "groups" in ddl), None)
        self.assertIsNotNone(groups_ddl)
        self.assertIn("away_summary", groups_ddl)
        
        # Check group DDL messages table contains meta
        messages_ddl = next((ddl for ddl in _GROUP_DDL if "CREATE TABLE" in ddl and "messages" in ddl), None)
        self.assertIsNotNone(messages_ddl)
        self.assertIn("meta", messages_ddl)

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_recap_debounce_and_deduplication(self, mock_call_ai):
        mock_call_ai.return_value = {"content": "Debounced summary."}
        
        # First call: generates recap
        summary1 = await generate_and_cache_recap(1)
        self.assertEqual(summary1, "Debounced summary.")
        self.assertEqual(mock_call_ai.call_count, 1)
        
        # Second call immediately after: debounced (returns None)
        summary2 = await generate_and_cache_recap(1)
        self.assertIsNone(summary2)
        self.assertEqual(mock_call_ai.call_count, 1)
        
        # Reset debounce state to allow generating again
        from core.recap.generator import _last_generated, _generating_groups
        _last_generated.clear()
        _generating_groups.clear()
        
        # Third call: runs again
        summary3 = await generate_and_cache_recap(1)
        self.assertEqual(summary3, "Debounced summary.")
        self.assertEqual(mock_call_ai.call_count, 2)

    @patch("core.recap.generator.call_ai_once", new_callable=AsyncMock)
    async def test_force_bypasses_debounce(self, mock_call_ai):
        """force=True 用于用户手动触发：跳过 5s 去抖，必定重算（不被静默跳过）。"""
        mock_call_ai.return_value = {"content": "Forced summary."}

        self.assertEqual(await generate_and_cache_recap(1), "Forced summary.")
        self.assertEqual(mock_call_ai.call_count, 1)

        # 紧接着的 eager 调用：被去抖跳过
        self.assertIsNone(await generate_and_cache_recap(1))
        self.assertEqual(mock_call_ai.call_count, 1)

        # 紧接着的 force 调用：绕过去抖，重新生成
        self.assertEqual(await generate_and_cache_recap(1, force=True), "Forced summary.")
        self.assertEqual(mock_call_ai.call_count, 2)


class TestRecapApi(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}

        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

        database.DB_PATH = TEST_DB_PATH
        await database.init_db()

        async with database.get_db() as db_conn:
            await db_conn.execute("INSERT INTO groups (id, name, away_summary) VALUES (1, 'Test Group', 'Cached Recap')")
            await db_conn.commit()

    async def asyncTearDown(self):
        from core import auth as _auth
        app.dependency_overrides.pop(_auth.get_current_user, None)

        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    async def test_get_recap_success(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/api/groups/1/recap")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"group_id": 1, "away_summary": "Cached Recap"})

    async def test_get_recap_not_found(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/api/groups/999/recap")
            self.assertEqual(resp.status_code, 404)

    @patch("core.recap.clear_recap", new_callable=AsyncMock)
    async def test_delete_recap(self, mock_clear_recap):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.delete("/api/groups/1/recap")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"ok": True})
            mock_clear_recap.assert_called_once_with(1)

    @patch("core.recap.generate_and_cache_recap", new_callable=AsyncMock)
    async def test_trigger_recap(self, mock_generate_recap):
        mock_generate_recap.return_value = "Triggered Summary"
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.post("/api/groups/1/recap/trigger")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"ok": True, "away_summary": "Triggered Summary"})
            mock_generate_recap.assert_called_once_with(1, force=True)

    @patch("core.recap.generate_and_cache_recap", new_callable=AsyncMock)
    async def test_trigger_recap_falls_back_to_cache_when_skipped(self, mock_generate_recap):
        # 生成被去抖/在途跳过返回 None 时，端点回退到现有缓存，绝不把 banner 清空。
        mock_generate_recap.return_value = None
        async with AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.post("/api/groups/1/recap/trigger")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"ok": True, "away_summary": "Cached Recap"})


if __name__ == "__main__":
    unittest.main()
