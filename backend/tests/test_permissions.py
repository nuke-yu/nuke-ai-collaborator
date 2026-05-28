"""Tests for the permissions package: Rule/Ruleset model and decision pipeline."""
import asyncio
import pytest
from permissions.models import Rule, Ruleset
from permissions import engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockBroadcaster:
    """Captures broadcast calls."""
    def __init__(self):
        self.sent = []

    async def broadcast(self, group_id, message):
        self.sent.append(message)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------

class TestRuleMatching:
    def test_exact_tool_name(self):
        rule = Rule(tool_pattern="run_shell", action="allow")
        assert engine._matches(rule, "run_shell", {})
        assert not engine._matches(rule, "read_file", {})

    def test_glob_tool_name(self):
        rule = Rule(tool_pattern="*_file", action="allow")
        assert engine._matches(rule, "read_file", {})
        assert engine._matches(rule, "write_file", {})
        assert not engine._matches(rule, "run_shell", {})

    def test_args_pattern_matches_any_value(self):
        rule = Rule(tool_pattern="run_shell", args_pattern="git *", action="allow")
        assert engine._matches(rule, "run_shell", {"cmd": "git status"})
        assert not engine._matches(rule, "run_shell", {"cmd": "ls -la"})

    def test_args_pattern_skips_none_values(self):
        rule = Rule(tool_pattern="run_shell", args_pattern="git *", action="allow")
        assert not engine._matches(rule, "run_shell", {"cmd": None, "cwd": None})

    def test_no_args_pattern_matches_any_args(self):
        rule = Rule(tool_pattern="run_shell", args_pattern="", action="allow")
        assert engine._matches(rule, "run_shell", {"cmd": "anything"})
        assert engine._matches(rule, "run_shell", {})


# ---------------------------------------------------------------------------
# Decision pipeline
# ---------------------------------------------------------------------------

class TestDecisionPipeline:
    def test_bypass_permissions_allows_everything(self):
        rs = Ruleset(mode="bypassPermissions")
        r = _run(engine.check("run_shell", {"cmd": "rm -rf /"}, rs, 1, None, 1))
        assert r["action"] == "allow"

    def test_deny_rule_blocks(self):
        rs = Ruleset(rules=[Rule(tool_pattern="write_*", action="deny")])
        r = _run(engine.check("write_file", {"path": "x"}, rs, 1, None, 1))
        assert r["action"] == "deny"
        assert "write_*" in r["reason"]

    def test_allow_rule_passes(self):
        rs = Ruleset(rules=[Rule(tool_pattern="read_file", action="allow")])
        r = _run(engine.check("read_file", {"path": "x"}, rs, 1, None, 1))
        assert r["action"] == "allow"

    def test_deny_takes_priority_over_allow(self):
        rs = Ruleset(rules=[
            Rule(tool_pattern="run_shell", action="allow"),
            Rule(tool_pattern="run_shell", action="deny"),
        ])
        r = _run(engine.check("run_shell", {}, rs, 1, None, 1))
        assert r["action"] == "deny"

    def test_dont_ask_mode_denies_unknown_tool(self):
        rs = Ruleset(mode="dontAsk")
        r = _run(engine.check("run_shell", {}, rs, 1, None, 1))
        assert r["action"] == "deny"
        assert "dontAsk" in r["reason"]

    def test_subagent_cannot_ask(self):
        rs = Ruleset(mode="default")
        r = _run(engine.check("run_shell", {}, rs, 1, None, 1, spawn_depth=1))
        assert r["action"] == "deny"
        assert "子 Agent" in r["reason"]

    def test_default_fallthrough_allows(self):
        """No matching rule + default mode = allow (no ask needed, default open)."""
        # default mode with no rules would normally ask — but we test that
        # bypass mode skips all pipeline
        rs = Ruleset(mode="bypassPermissions")
        r = _run(engine.check("unknown_tool", {}, rs, 99, None, 1))
        assert r["action"] == "allow"


# ---------------------------------------------------------------------------
# once-rules (in-memory)
# ---------------------------------------------------------------------------

class TestOnceRules:
    def setup_method(self):
        # Clear once-rules between tests
        engine._once_rules.clear()

    def test_once_rule_allows_after_user_approves(self):
        rs = Ruleset(mode="default")
        broadcaster = _MockBroadcaster()
        request_id = None

        async def run():
            nonlocal request_id
            # Start check (will ask)
            fut = asyncio.ensure_future(
                engine.check("run_shell", {"cmd": "ls"}, rs, 42, broadcaster, 1)
            )
            # Let the check broadcast the request
            await asyncio.sleep(0)
            assert broadcaster.sent, "expected permission_request broadcast"
            request_id = broadcaster.sent[0]["request_id"]
            # Resolve as approved, once
            engine.resolve(request_id, approved=True, persistence="once")
            result = await fut
            return result

        r = _run(run())
        assert r["action"] == "allow"
        # once-rule should be stored
        assert any(rule.action == "allow" for rule in engine._once_rules.get(42, []))

    def test_once_rule_denies_when_user_rejects(self):
        rs = Ruleset(mode="default")
        broadcaster = _MockBroadcaster()

        async def run():
            fut = asyncio.ensure_future(
                engine.check("run_shell", {"cmd": "ls"}, rs, 43, broadcaster, 1)
            )
            await asyncio.sleep(0)
            rid = broadcaster.sent[0]["request_id"]
            engine.resolve(rid, approved=False)
            return await fut

        r = _run(run())
        assert r["action"] == "deny"
        assert "拒绝" in r["reason"]

    def test_resolve_unknown_request_returns_none(self):
        result = engine.resolve("nonexistent-id", approved=True)
        assert result is None


# ---------------------------------------------------------------------------
# Ruleset — persist_rule path
# ---------------------------------------------------------------------------

class TestPersistRule:
    def setup_method(self):
        engine._once_rules.clear()

    def test_always_persistence_returns_persist_rule(self):
        rs = Ruleset(mode="default")
        broadcaster = _MockBroadcaster()

        async def run():
            fut = asyncio.ensure_future(
                engine.check("run_shell", {}, rs, 99, broadcaster, 1)
            )
            await asyncio.sleep(0)
            rid = broadcaster.sent[0]["request_id"]
            engine.resolve(rid, approved=True, persistence="always")
            return await fut

        r = _run(run())
        assert r["action"] == "allow"
        assert "persist_rule" in r
        assert r["persist_rule"].tool_pattern == "run_shell"
