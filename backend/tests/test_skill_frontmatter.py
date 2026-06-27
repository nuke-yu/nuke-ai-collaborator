"""Tests for fork skill frontmatter handling in tool_loop_v1."""
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------

class TestRunForkSkillWithToolSchemas(unittest.IsolatedAsyncioTestCase):

    async def test_fork_no_schemas_passes_none(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        
        mock_ai = MagicMock()
        mock_ai.call = AsyncMock(return_value={"type": "text", "content": "ok", "usage": {}})
        
        result = await _run_fork_skill("sp", "task", "claude", "claude-opus-4-7", 0.7, mock_ai)
        
        self.assertEqual(result, "ok")
        # Verify call used tools=None
        self.assertIsNone(mock_ai.call.call_args[1].get("tools"))

    async def test_fork_with_schemas_passes_them(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        
        mock_ai = MagicMock()
        mock_ai.call = AsyncMock(return_value={"type": "text", "content": "done", "usage": {}})
        schemas = [{"name": "run_shell", "description": "run shell"}]
        
        result = await _run_fork_skill("sp", "task", "claude", "claude-opus-4-7", 0.7,
                                       mock_ai, tool_schemas=schemas)

        self.assertEqual(result, "done")
        self.assertEqual(mock_ai.call.call_args[1].get("tools"), schemas)

    async def test_fork_tool_calls_dispatched_then_final_text(self):
        # Plan A §7.5.2: a fork that declares tools now runs a real multi-turn
        # loop — it dispatches the requested tool and continues until the model
        # returns text (no more "fork doesn't support tool loops" placeholder).
        from executors.plugins.tool_loop_v1 import _run_fork_skill

        mock_ai = MagicMock()
        mock_ai.call = AsyncMock(side_effect=[
            {"type": "tool_calls",
             "calls": [{"id": "c1", "name": "run_shell", "arguments": {"cmd": "ls"}}],
             "assistant_message": {"role": "assistant", "content": ""}},
            {"type": "text", "content": "all done"},
        ])

        async def fake_dispatch(name, args, ctx):
            return ("ok", False)

        with patch("executors.tool_dispatch.dispatch_tool", new=fake_dispatch):
            result = await _run_fork_skill(
                "sp", "task", "claude", "claude-opus-4-7", 0.7,
                mock_ai, tool_schemas=[{"name": "run_shell"}],
            )

        self.assertEqual(result, "all done")


class TestModelWindowSuffix(unittest.TestCase):
    def test_strips_1m_suffix(self):
        from skills.metadata import strip_context_window_suffix
        self.assertEqual(strip_context_window_suffix("claude-opus-4-8[1m]"), "claude-opus-4-8")
        self.assertEqual(strip_context_window_suffix("claude-opus-4-8 [1m]"), "claude-opus-4-8")

    def test_leaves_plain_model_untouched(self):
        from skills.metadata import strip_context_window_suffix
        self.assertEqual(strip_context_window_suffix("deepseek-chat"), "deepseek-chat")
        self.assertEqual(strip_context_window_suffix(""), "")


class TestPlatformsVersionParsing(unittest.TestCase):
    def _meta(self, body):
        import tempfile
        from pathlib import Path
        from skills.metadata import parse_skill_meta
        d = tempfile.mkdtemp()
        p = Path(d) / "SKILL.md"
        p.write_text(body, encoding="utf-8")
        return parse_skill_meta(p)

    def test_parses_platforms_and_version(self):
        meta = self._meta(
            "---\nname: x\ndescription: d\nplatforms: posix\nversion: 1.2.3\nshell: powershell\n---\nbody"
        )
        self.assertEqual(meta["platforms"], "posix")
        self.assertEqual(meta["version"], "1.2.3")
        self.assertEqual(meta["shell"], "powershell")

    def test_defaults_when_absent(self):
        meta = self._meta("---\nname: x\ndescription: d\n---\nbody")
        self.assertEqual(meta["platforms"], "pure")
        self.assertEqual(meta["version"], "")
        self.assertEqual(meta["shell"], "bash")


if __name__ == "__main__":
    unittest.main()
