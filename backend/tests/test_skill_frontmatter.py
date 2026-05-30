"""Unit tests for M3/M4 Skill frontmatter fields: user-invocable, argument-hint, allowed-tools, model."""
import sys
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.metadata import parse_frontmatter, parse_skill_meta


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatterNewFields(unittest.TestCase):

    def test_user_invocable_false(self):
        content = "---\nname: my-skill\nuser-invocable: false\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertFalse(fm["user_invocable"])

    def test_user_invocable_no(self):
        content = "---\nname: my-skill\nuser-invocable: no\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertFalse(fm["user_invocable"])

    def test_user_invocable_0(self):
        content = "---\nname: my-skill\nuser-invocable: 0\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertFalse(fm["user_invocable"])

    def test_user_invocable_true(self):
        content = "---\nname: my-skill\nuser-invocable: true\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertTrue(fm["user_invocable"])

    def test_user_invocable_yes(self):
        content = "---\nname: my-skill\nuser-invocable: yes\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertTrue(fm["user_invocable"])

    def test_argument_hint_parsed(self):
        content = "---\nname: my-skill\nargument-hint: <url> the target URL\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertEqual(fm["argument_hint"], "<url> the target URL")

    def test_allowed_tools_single(self):
        content = "---\nname: my-skill\nallowed-tools: run_shell\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertEqual(fm["allowed_tools"], ["run_shell"])

    def test_allowed_tools_multiple(self):
        content = "---\nname: my-skill\nallowed-tools: run_shell, read_file, write_file\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertEqual(fm["allowed_tools"], ["run_shell", "read_file", "write_file"])

    def test_allowed_tools_strips_whitespace(self):
        content = "---\nname: my-skill\nallowed-tools:  run_shell ,  read_file \n---\nbody"
        fm = parse_frontmatter(content)
        self.assertEqual(fm["allowed_tools"], ["run_shell", "read_file"])

    def test_allowed_tools_empty_string(self):
        content = "---\nname: my-skill\nallowed-tools: \n---\nbody"
        fm = parse_frontmatter(content)
        self.assertEqual(fm["allowed_tools"], [])

    def test_no_new_fields_absent(self):
        content = "---\nname: my-skill\ndescription: just a skill\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertNotIn("user_invocable", fm)
        self.assertNotIn("argument_hint", fm)
        self.assertNotIn("allowed_tools", fm)
        self.assertNotIn("model", fm)

    def test_model_parsed(self):
        content = "---\nname: my-skill\nmodel: claude-opus-4-7\n---\nbody"
        fm = parse_frontmatter(content)
        self.assertEqual(fm["model"], "claude-opus-4-7")

    def test_model_empty_string(self):
        content = "---\nname: my-skill\nmodel: \n---\nbody"
        fm = parse_frontmatter(content)
        self.assertEqual(fm.get("model"), "")


# ---------------------------------------------------------------------------
# parse_skill_meta
# ---------------------------------------------------------------------------

class TestParseSkillMetaNewFields(unittest.TestCase):

    def _write_skill(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "SKILL.md"
        p.write_text(content, encoding="utf-8")
        return p

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_defaults_when_fields_absent(self):
        p = self._write_skill(self.tmp, "---\nname: s\n---\nHello")
        meta = parse_skill_meta(p)
        self.assertTrue(meta["user_invocable"])
        self.assertEqual(meta["argument_hint"], "")
        self.assertEqual(meta["allowed_tools"], [])
        self.assertEqual(meta["model"], "")

    def test_user_invocable_false_propagated(self):
        p = self._write_skill(self.tmp, "---\nname: s\nuser-invocable: false\n---\nHello")
        meta = parse_skill_meta(p)
        self.assertFalse(meta["user_invocable"])

    def test_argument_hint_propagated(self):
        p = self._write_skill(self.tmp, "---\nname: s\nargument-hint: <topic>\n---\nHello")
        meta = parse_skill_meta(p)
        self.assertEqual(meta["argument_hint"], "<topic>")

    def test_allowed_tools_propagated(self):
        p = self._write_skill(self.tmp, "---\nname: s\nallowed-tools: run_shell, read_file\n---\nHello")
        meta = parse_skill_meta(p)
        self.assertEqual(meta["allowed_tools"], ["run_shell", "read_file"])

    def test_model_propagated(self):
        p = self._write_skill(self.tmp, "---\nname: s\nmodel: claude-haiku-4-5-20251001\n---\nHello")
        meta = parse_skill_meta(p)
        self.assertEqual(meta["model"], "claude-haiku-4-5-20251001")


# ---------------------------------------------------------------------------
# _build_skills_xml — argument_hint injected; user_invocable=False excluded
# ---------------------------------------------------------------------------

class TestBuildSkillsXml(unittest.TestCase):

    def _build(self, skills):
        # Import inline to avoid circular-import issues at module level
        from executors.plugins.tool_loop_v1 import _build_skills_xml
        return _build_skills_xml(skills, "deepseek-chat")

    def test_argument_hint_appears_in_xml(self):
        skills = [
            {
                "name": "web-search",
                "description": "Search the web",
                "argument_hint": "<query> your search terms",
            }
        ]
        xml, included = self._build(skills)
        self.assertIn("web-search", included)
        self.assertIn("<argument_hint><query> your search terms</argument_hint>", xml)

    def test_no_argument_hint_when_absent(self):
        skills = [{"name": "plain", "description": "A plain skill"}]
        xml, included = self._build(skills)
        self.assertNotIn("argument_hint", xml)

    def test_user_invocable_false_already_excluded_from_candidates(self):
        # The lazy_candidates list should already exclude user_invocable=False skills,
        # so _build_skills_xml should not receive them. Verify nothing breaks if one
        # accidentally appears: it still gets included (filtering is upstream).
        skills = [
            {"name": "hidden", "description": "hidden skill", "user_invocable": False},
            {"name": "visible", "description": "visible skill"},
        ]
        xml, included = self._build(skills)
        # Both are in included because filtering happens before calling _build_skills_xml
        self.assertIn("hidden", included)
        self.assertIn("visible", included)


# ---------------------------------------------------------------------------
# loader.run_skill — ctx side-effects for allowed_tools
# ---------------------------------------------------------------------------

class TestRunSkillAllowedToolsSideEffect(unittest.IsolatedAsyncioTestCase):

    def _make_bot_ws(self, tmp_path: Path) -> Path:
        ws = tmp_path / "ws" / "bots" / "42"
        ws.mkdir(parents=True)
        return ws

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_skill_file(self, ws: Path, content: str) -> None:
        skills_dir = ws / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "test-skill.md").write_text(content, encoding="utf-8")

    async def test_allowed_tools_set_in_ctx_for_inline_skill(self):
        ws = self._make_bot_ws(self.tmp)
        self._write_skill_file(ws, "---\nname: test-skill\nallowed-tools: run_shell, read_file\n---\nDo something")

        ctx = {}
        from skills import loader as _loader
        with patch.object(_loader, "bot_ws", return_value=ws), \
             patch("skills.discovery.bot_ws", return_value=ws), \
             patch.object(_loader, "process_skill_content", new=AsyncMock(return_value="processed")):
            result = await _loader.run_skill(42, "test-skill", "", ctx=ctx)

        self.assertEqual(ctx.get("skill_allowed_tools"), ["run_shell", "read_file"])
        self.assertNotEqual(result, "__SKILL_FORK__")

    async def test_no_allowed_tools_ctx_key_absent(self):
        ws = self._make_bot_ws(self.tmp)
        self._write_skill_file(ws, "---\nname: test-skill\n---\nDo something")

        ctx = {}
        from skills import loader as _loader
        with patch.object(_loader, "bot_ws", return_value=ws), \
             patch("skills.discovery.bot_ws", return_value=ws), \
             patch.object(_loader, "process_skill_content", new=AsyncMock(return_value="processed")):
            await _loader.run_skill(42, "test-skill", "", ctx=ctx)

        self.assertNotIn("skill_allowed_tools", ctx)

    async def test_fork_skill_carries_allowed_tools(self):
        ws = self._make_bot_ws(self.tmp)
        self._write_skill_file(ws, "---\nname: test-skill\ncontext: fork\nallowed-tools: run_shell\n---\nDo something")

        ctx = {}
        from skills import loader as _loader
        with patch.object(_loader, "bot_ws", return_value=ws), \
             patch("skills.discovery.bot_ws", return_value=ws), \
             patch.object(_loader, "process_skill_content", new=AsyncMock(return_value="processed")):
            result = await _loader.run_skill(42, "test-skill", "my-args", ctx=ctx)

        self.assertEqual(result, "__SKILL_FORK__")
        fork_info = ctx.get("skill_fork", {})
        self.assertEqual(fork_info.get("allowed_tools"), ["run_shell"])
        # inline key should NOT be set for fork skills
        self.assertNotIn("skill_allowed_tools", ctx)

    async def test_model_set_in_ctx_for_inline_skill(self):
        ws = self._make_bot_ws(self.tmp)
        self._write_skill_file(ws, "---\nname: test-skill\nmodel: claude-opus-4-7\n---\nDo something")

        ctx = {}
        from skills import loader as _loader
        with patch.object(_loader, "bot_ws", return_value=ws), \
             patch("skills.discovery.bot_ws", return_value=ws), \
             patch.object(_loader, "process_skill_content", new=AsyncMock(return_value="processed")):
            await _loader.run_skill(42, "test-skill", "", ctx=ctx)

        self.assertEqual(ctx.get("skill_model"), "claude-opus-4-7")

    async def test_no_model_ctx_key_absent(self):
        ws = self._make_bot_ws(self.tmp)
        self._write_skill_file(ws, "---\nname: test-skill\n---\nDo something")

        ctx = {}
        from skills import loader as _loader
        with patch.object(_loader, "bot_ws", return_value=ws), \
             patch("skills.discovery.bot_ws", return_value=ws), \
             patch.object(_loader, "process_skill_content", new=AsyncMock(return_value="processed")):
            await _loader.run_skill(42, "test-skill", "", ctx=ctx)

        self.assertNotIn("skill_model", ctx)

    async def test_fork_skill_carries_model(self):
        ws = self._make_bot_ws(self.tmp)
        self._write_skill_file(ws, "---\nname: test-skill\ncontext: fork\nmodel: claude-haiku-4-5-20251001\n---\nDo something")

        ctx = {}
        from skills import loader as _loader
        with patch.object(_loader, "bot_ws", return_value=ws), \
             patch("skills.discovery.bot_ws", return_value=ws), \
             patch.object(_loader, "process_skill_content", new=AsyncMock(return_value="processed")):
            result = await _loader.run_skill(42, "test-skill", "my-args", ctx=ctx)

        self.assertEqual(result, "__SKILL_FORK__")
        fork_info = ctx.get("skill_fork", {})
        self.assertEqual(fork_info.get("model"), "claude-haiku-4-5-20251001")


# ---------------------------------------------------------------------------
# _run_fork_skill — tool_schemas parameter
# ---------------------------------------------------------------------------

class TestRunForkSkillWithToolSchemas(unittest.IsolatedAsyncioTestCase):

    async def test_fork_no_schemas_passes_none(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        captured = {}

        async def fake_call_ai_once(system_prompt, messages, provider, model,
                                    temperature, max_tokens, tools=None, **kwargs):
            captured["tools"] = tools
            return {"type": "text", "content": "ok"}

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            result = await _run_fork_skill("sp", "task", "claude", "claude-opus-4-7", 0.7)

        self.assertIsNone(captured["tools"])
        self.assertEqual(result, "ok")

    async def test_fork_with_schemas_passes_them(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill
        captured = {}
        schemas = [{"name": "run_shell", "description": "run shell"}]

        async def fake_call_ai_once(system_prompt, messages, provider, model,
                                    temperature, max_tokens, tools=None, **kwargs):
            captured["tools"] = tools
            return {"type": "text", "content": "done"}

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            result = await _run_fork_skill("sp", "task", "claude", "claude-opus-4-7", 0.7,
                                           tool_schemas=schemas)

        self.assertEqual(captured["tools"], schemas)
        self.assertEqual(result, "done")

    async def test_fork_tool_calls_result_returns_summary(self):
        from executors.plugins.tool_loop_v1 import _run_fork_skill

        async def fake_call_ai_once(system_prompt, messages, provider, model,
                                    temperature, max_tokens, tools=None, **kwargs):
            return {
                "type": "tool_calls",
                "calls": [{"name": "run_shell"}],
                "assistant_message": {},
            }

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once):
            result = await _run_fork_skill("sp", "task", "claude", "claude-opus-4-7", 0.7,
                                           tool_schemas=[{"name": "run_shell"}])

        self.assertIn("run_shell", result)
        self.assertIn("fork", result)


if __name__ == "__main__":
    unittest.main()
