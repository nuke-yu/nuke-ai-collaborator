import os
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
from executors.base import InteractionAdapter

_HERE = Path(__file__).parent.parent if 'Path' in locals() else os.path.dirname(__file__)
_TEST_DB = str(os.path.join(os.path.dirname(_HERE), "test_dynamic_context.db"))

class MockInteraction(InteractionAdapter):
    async def broadcast(self, group_id, payload): pass
    async def save_message(self, group_id, member_id, content, **kwargs): return 1
    async def append_session_event(self, session_id, event_type, payload): pass
    async def save_session_snapshot(self, session_id, messages): pass
    async def update_session_tokens(self, session_id, **usage): pass
    async def create_session(self, **kwargs): pass
    async def update_session_status(self, session_id, status): pass

class TestDynamicContext(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _db_mod.DB_PATH = _TEST_DB
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
            # Seed parents
            await db.execute("INSERT INTO groups (id, name) VALUES (1, 'g')")
            await db.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (1, 1, 'WorkerBot', 'bot', 'dev')")
            await db.commit()

        # Prevent _ws.load_group_context from hitting the real filesystem
        self._load_group_ctx_patcher = patch(
            "executors.plugins.tool_loop_v1._ws.load_group_context",
            new=AsyncMock(return_value=""),
        )
        self._load_group_ctx_patcher.start()

    async def asyncTearDown(self):
        self._load_group_ctx_patcher.stop()
        _db_mod.DB_PATH = self._orig
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)

    async def test_tool_loop_refreshes_context_each_iter(self):
        from executors.plugins.tool_loop_v1 import ToolLoopV1
        from executors.base import ExecutionContext
        
        bot = {
            "id": 1, "name": "Bot", "role": "dev", "avatar_color": "#fff",
            "type": "bot", "system_prompt": "orig", "model_provider": "deepseek",
            "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096,
            "executor_config": {},
        }
        ctx = ExecutionContext(
            bot=bot, group_id=1, user_message="task",
            sender={"id": 2, "name": "Human", "type": "human"},
            history=[], all_bots=[bot], all_members=[bot],
            interaction=MockInteraction(),
        )

        # Mock load_context_files to return different content on each call
        side_effects = [
            [{"source": "group", "name": "BOARD.md", "content": "ver 1"}],
            [{"source": "group", "name": "BOARD.md", "content": "ver 2"}],
            [{"source": "group", "name": "BOARD.md", "content": "ver 3"}],
            [{"source": "group", "name": "BOARD.md", "content": "ver 4"}],
            [{"source": "group", "name": "BOARD.md", "content": "ver 5"}],
            [{"source": "group", "name": "BOARD.md", "content": "ver 6"}],
            [{"source": "group", "name": "BOARD.md", "content": "ver 7"}],
            [{"source": "group", "name": "BOARD.md", "content": "ver 8"}],
        ]
        mock_load = AsyncMock(side_effect=side_effects)

        captured_prompts = []
        async def mock_call_ai(*args, **kwargs):
            captured_prompts.append(args[0]) # system_prompt is the first arg
            # Return a tool call for first two times to trigger more iterations
            if len(captured_prompts) < 3:
                return {
                    "type": "tool_calls", 
                    "calls": [{"id": f"tc{len(captured_prompts)}", "name": "read_file", "arguments": {"path": "a.txt"}}],
                    "assistant_message": {"role": "assistant", "content": "calling tool", "tool_calls": []},
                    "usage": {}
                }
            return {"type": "text", "content": "done", "usage": {}}

        m = "executors.plugins.tool_loop_v1."
        with patch("workspace.load_context_files", new=mock_load), \
             patch("core.orchestration.ai_service.call_ai_once", new=AsyncMock(side_effect=mock_call_ai)), \
             patch("workspace.layout.get_group_language", return_value="zh"), \
             patch(m + "load_always_skills", new=AsyncMock(return_value=[])), \
             patch(m + "append_log", new=AsyncMock()), \
             patch(m + "archive_run", new=AsyncMock()), \
             patch("executors.plugins.tool_loop_v1.tool_executor.execute", new=AsyncMock(return_value=("tool result", False))), \
             patch("executors.plugins.tool_loop_v1.tool_executor.get_schemas", return_value=[{"function": {"name": "read_file"}}]):
            
            executor = ToolLoopV1()
            await executor.run(ctx)

        # Verify load_context_files was called multiple times
        # 3 (loop starts) + 2 (auto_compact calls in first 2 tool rounds) = 5
        self.assertEqual(mock_load.call_count, 5)
        
        # Verify that each AI call received a different version of the context
        self.assertIn("ver 1", captured_prompts[0])
        self.assertIn("ver 3", captured_prompts[1])
        self.assertIn("ver 5", captured_prompts[2])

class TestGetFreshContextPrefixGroupCtx(unittest.IsolatedAsyncioTestCase):
    async def test_group_ctx_not_in_per_iter_prefix(self):
        """_get_fresh_context_prefix must NOT include shared workspace context.
        Group context is injected once into system_prompt_base during _setup_session,
        so adding it here would cause it to appear twice in every AI call."""
        import tempfile, pathlib
        from skills import constants as _c
        orig_root = _c.WORKSPACE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _c.WORKSPACE_ROOT = pathlib.Path(tmp)
            try:
                import workspace as ws_mod
                shared = ws_mod.group_workspace(5)
                (shared / "BOARD.md").write_text("# Board content")

                from executors.plugins.tool_loop_v1 import ToolLoopRunner, ToolLoopV1
                from executors.base import ExecutionContext

                ctx = ExecutionContext(
                    bot={"id": 1, "name": "Dev", "type": "bot"},
                    group_id=5,
                    user_message="hello",
                    sender={"id": 0, "name": "user", "type": "human"},
                    history=[],
                    all_bots=[],
                    all_members=[],
                )
                runner = ToolLoopRunner.__new__(ToolLoopRunner)
                runner.bot = ctx.bot
                runner.ctx = ctx
                runner.executor = ToolLoopV1()
                runner.skills_xml = ""

                prefix, _ = await runner._get_fresh_context_prefix()
                # Group context must NOT be in the per-iteration prefix to avoid duplication
                self.assertNotIn("# Board content", prefix)
                self.assertNotIn("【共享工作区目录】", prefix)
                self.assertNotIn("【工作看板】", prefix)
            finally:
                _c.WORKSPACE_ROOT = orig_root


if __name__ == "__main__":
    unittest.main()
