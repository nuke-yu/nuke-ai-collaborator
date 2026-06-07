"""Tests for #5: 'always allow' synthesizes a scoped args_pattern, not a blanket
allow-all-args rule (which would let approving `git status` auto-allow `rm -rf /`).
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permissions.patterns import synthesize_args_pattern
from permissions.models import Rule, Ruleset
from permissions import engine


def _run(coro):
    return asyncio.run(coro)


class TestSynthesizeArgsPattern(unittest.TestCase):

    def test_shell_subcommand_depth_git(self):
        self.assertEqual(
            synthesize_args_pattern("run_shell", {"cmd": "git push origin main"}),
            "git push *",
        )

    def test_shell_depth1_with_args(self):
        self.assertEqual(synthesize_args_pattern("run_shell", {"cmd": "ls -la"}), "ls *")

    def test_shell_single_token_exact(self):
        self.assertEqual(synthesize_args_pattern("run_shell", {"cmd": "pwd"}), "pwd")

    def test_shell_subcommand_only_exact(self):
        self.assertEqual(synthesize_args_pattern("run_shell", {"cmd": "git push"}), "git push")

    def test_shell_strips_wrappers_and_env(self):
        self.assertEqual(
            synthesize_args_pattern("run_shell", {"cmd": "sudo rm -rf /tmp/x"}), "rm *"
        )
        self.assertEqual(
            synthesize_args_pattern("run_shell", {"cmd": "env X=1 npm run build"}), "npm run *"
        )

    def test_shell_docker_compose_depth2(self):
        self.assertEqual(
            synthesize_args_pattern("run_shell", {"cmd": "docker compose up -d"}),
            "docker compose *",
        )

    def test_shell_empty_is_blanket(self):
        self.assertEqual(synthesize_args_pattern("run_shell", {"cmd": ""}), "")

    def test_path_tool_exact_path(self):
        self.assertEqual(
            synthesize_args_pattern("write_file", {"path": "/repo/src/app.py", "content": "x"}),
            "/repo/src/app.py",
        )

    def test_path_glob_chars_escaped(self):
        pat = synthesize_args_pattern("write_file", {"path": "/repo/[x].py"})
        import fnmatch
        self.assertTrue(fnmatch.fnmatch("/repo/[x].py", pat))   # literal path matches
        self.assertFalse(fnmatch.fnmatch("/repo/q.py", pat))    # not treated as wildcard

    def test_spawn_agent_exact_name(self):
        self.assertEqual(synthesize_args_pattern("spawn_agent", {"bot_name": "dev"}), "dev")

    def test_unknown_tool_blanket(self):
        self.assertEqual(synthesize_args_pattern("some_tool", {"x": 1}), "")


class TestScopedRuleMatching(unittest.TestCase):
    """The whole point: a synthesized `git push *` rule must NOT auto-allow
    unrelated dangerous commands."""

    def test_scoped_rule_blocks_unrelated_command(self):
        rule = Rule(tool_pattern="run_shell", args_pattern="git push *", action="allow")
        self.assertTrue(engine._matches(rule, "run_shell", {"cmd": "git push origin main"}))
        self.assertTrue(engine._matches(rule, "run_shell", {"cmd": "git push --force"}))
        self.assertFalse(engine._matches(rule, "run_shell", {"cmd": "rm -rf /"}))
        self.assertFalse(engine._matches(rule, "run_shell", {"cmd": "git status"}))

    def test_blanket_empty_still_matches_all(self):
        # documents the OLD behavior we moved away from
        blanket = Rule(tool_pattern="run_shell", args_pattern="", action="allow")
        self.assertTrue(engine._matches(blanket, "run_shell", {"cmd": "rm -rf /"}))


class TestAlwaysPersistsScopedPattern(unittest.TestCase):

    def setUp(self):
        engine._once_grants.clear()
        engine._pending.clear()

    def test_always_persist_uses_synthesized_pattern(self):
        class _Bc:
            def __init__(self): self.sent = []
            async def broadcast(self, gid, payload): self.sent.append(payload)

        rs = Ruleset(mode="default")
        bc = _Bc()

        async def run():
            fut = asyncio.ensure_future(
                engine.check("run_shell", {"cmd": "git push origin main"}, rs, 99, bc, 1)
            )
            await asyncio.sleep(0)
            engine.resolve(bc.sent[0]["request_id"], approved=True, persistence="always")
            return await fut

        r = _run(run())
        self.assertEqual(r["persist_rule"].tool_pattern, "run_shell")
        self.assertEqual(r["persist_rule"].args_pattern, "git push *")


if __name__ == "__main__":
    unittest.main()
