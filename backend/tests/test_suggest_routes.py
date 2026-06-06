import unittest
import os
import sys
import shutil
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database

# Configure test database paths
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_chat.db")

database.DB_PATH = TEST_DB_PATH

from main import app
from httpx import AsyncClient

class TestSuggestRoutes(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Backup DB path to avoid leak pollution
        self.original_db_path = database.DB_PATH
        database.DB_PATH = TEST_DB_PATH

        # Bypass auth
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        
        # Clear suggest globals to avoid test case pollution
        from core.suggest.suggest import _suggest_cache, _suggesting_groups
        _suggest_cache.clear()
        _suggesting_groups.clear()

        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
            
        await database.init_db()
        
        async with database.get_db() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'Test Group')")
            await db.execute(
                """INSERT INTO members (group_id, name, type, role, system_prompt, avatar_color, model_provider, model_name)
                   VALUES (1, 'SuperBot', 'bot', 'QA Engineer', 'You are QA', '#ff00ff', 'openai', 'gpt-4')"""
            )
            await db.commit()

    async def asyncTearDown(self):
        # Restore original DB path
        database.DB_PATH = self.original_db_path

        # Clear suggest globals to prevent leaking cache to other test suites
        from core.suggest.suggest import _suggest_cache, _suggesting_groups
        _suggest_cache.clear()
        _suggesting_groups.clear()

        from core import auth as _auth
        app.dependency_overrides.pop(_auth.get_current_user, None)
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    @patch("core.suggest.suggest.call_ai_once")
    async def test_suggest_endpoint(self, mock_call_ai):
        mock_call_ai.return_value = {
            "content": "- @SuperBot 请帮忙检查当前的构建状态\n- @SuperBot 运行测试"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/groups/1/suggest", json={"awaiting_confirm": "gate_123"})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("suggestions", data)
            self.assertEqual(
                data["suggestions"],
                ["@SuperBot 请帮忙检查当前的构建状态", "@SuperBot 运行测试"]
            )
            
            # Verify cache works (should not call AI a second time)
            mock_call_ai.reset_mock()
            response2 = await ac.post("/api/groups/1/suggest", json={"awaiting_confirm": "gate_123"})
            self.assertEqual(response2.status_code, 200)
            data2 = response2.json()
            self.assertEqual(
                data2["suggestions"],
                ["@SuperBot 请帮忙检查当前的构建状态", "@SuperBot 运行测试"]
            )
            mock_call_ai.assert_not_called()

    @patch("core.suggest.suggest.call_ai_once")
    async def test_suggest_endpoint_numbered_list(self, mock_call_ai):
        mock_call_ai.return_value = {
            "content": "1. @SuperBot check status\n2) @SuperBot run tests"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/groups/1/suggest", json={"awaiting_confirm": "gate_123"})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("suggestions", data)
            self.assertEqual(
                data["suggestions"],
                ["@SuperBot check status", "@SuperBot run tests"]
            )

    @patch("core.suggest.suggest.call_ai_once")
    async def test_suggest_endpoint_cache_invalidation_on_member_change(self, mock_call_ai):
        mock_call_ai.return_value = {
            "content": "- @SuperBot 请帮忙检查当前的构建状态"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/groups/1/suggest", json={"awaiting_confirm": "gate_123"})
            self.assertEqual(response.status_code, 200)
            
            # Now add another bot member to the database
            async with database.get_db() as db:
                await db.execute(
                    """INSERT INTO members (group_id, name, type, role, system_prompt, avatar_color, model_provider, model_name)
                       VALUES (1, 'SecondBot', 'bot', 'Dev', 'You are Dev', '#00ffff', 'openai', 'gpt-4')"""
                )
                await db.commit()
                
            # Verify cache is invalidated and LLM is called again
            mock_call_ai.reset_mock()
            mock_call_ai.return_value = {
                "content": "- @SecondBot 新的任务已分配"
            }
            response2 = await ac.post("/api/groups/1/suggest", json={"awaiting_confirm": "gate_123"})
            self.assertEqual(response2.status_code, 200)
            data2 = response2.json()
            self.assertEqual(data2["suggestions"], ["@SecondBot 新的任务已分配"])
            mock_call_ai.assert_called_once()

    @patch("core.suggest.suggest.call_ai_once")
    async def test_suggest_endpoint_empty_cooldown(self, mock_call_ai):
        # 1. First call returns empty list
        mock_call_ai.return_value = {
            "content": ""
        }
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/groups/1/suggest", json={"awaiting_confirm": "gate_123"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["suggestions"], [])
            self.assertEqual(mock_call_ai.call_count, 1)
            
            # 2. Second call immediately after should HIT the cache and NOT call AI
            mock_call_ai.reset_mock()
            response2 = await ac.post("/api/groups/1/suggest", json={"awaiting_confirm": "gate_123"})
            self.assertEqual(response2.status_code, 200)
            self.assertEqual(response2.json()["suggestions"], [])
            mock_call_ai.assert_not_called()
            
            # 3. Fast-forward time (mock time.time to be 31 seconds later)
            import time
            original_time = time.time
            try:
                # Mock time.time to return 31 seconds in the future
                future_time = time.time() + 31
                time.time = lambda: future_time
                
                mock_call_ai.reset_mock()
                mock_call_ai.return_value = {
                    "content": "- @SuperBot 重试成功"
                }
                response3 = await ac.post("/api/groups/1/suggest", json={"awaiting_confirm": "gate_123"})
                self.assertEqual(response3.status_code, 200)
                self.assertEqual(response3.json()["suggestions"], ["@SuperBot 重试成功"])
                mock_call_ai.assert_called_once()
            finally:
                time.time = original_time

if __name__ == "__main__":
    unittest.main()
