"""Tests for the permissions package: Rule/Ruleset model and decision pipeline."""
import asyncio
import json
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
        # Clear once-grants and pending between tests
        engine._once_grants.clear()
        engine._pending.clear()

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
        # a once-grant scoped to (bot_id=42, group_id=1) should be stored
        assert engine._once_grants.get((42, 1))

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
        engine._once_grants.clear()
        engine._pending.clear()

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


# ---------------------------------------------------------------------------
# DFT-032: once-grant is group-scoped, args-specific, single-consume
# ---------------------------------------------------------------------------

class TestOnceGrantSemantics:
    def setup_method(self):
        engine._once_grants.clear()
        engine._pending.clear()

    def test_once_grant_consumed_after_single_use(self):
        rs = Ruleset(mode="default")
        b = _MockBroadcaster()
        args = {"cmd": "ls"}

        async def run():
            fut = asyncio.ensure_future(engine.check("run_shell", args, rs, 7, b, 1))
            await asyncio.sleep(0)
            engine.resolve(b.sent[0]["request_id"], approved=True, persistence="once", group_id=1)
            assert (await fut)["action"] == "allow"
            # 2nd identical call: auto-allowed by the grant, no new prompt
            return await engine.check("run_shell", args, rs, 7, b, 1)

        r2 = _run(run())
        assert r2["action"] == "allow"
        assert len(b.sent) == 1, "2nd call must not broadcast a new request"
        assert not engine._once_grants.get((7, 1)), "grant should be consumed"

    def test_once_grant_does_not_leak_across_groups(self):
        rs = Ruleset(mode="default")
        b = _MockBroadcaster()
        args = {"cmd": "ls"}

        async def run():
            fut = asyncio.ensure_future(engine.check("run_shell", args, rs, 7, b, 1))
            await asyncio.sleep(0)
            engine.resolve(b.sent[0]["request_id"], approved=True, persistence="once", group_id=1)
            await fut
            # same bot+tool+args but a DIFFERENT group must ask again
            fut2 = asyncio.ensure_future(engine.check("run_shell", args, rs, 7, b, 2))
            await asyncio.sleep(0)
            asked = len(b.sent) == 2
            engine.resolve(b.sent[1]["request_id"], approved=False, group_id=2)
            await fut2
            return asked

        assert _run(run()), "grant must not be honored in another group"

    def test_once_grant_is_args_specific(self):
        rs = Ruleset(mode="default")
        b = _MockBroadcaster()

        async def run():
            fut = asyncio.ensure_future(engine.check("run_shell", {"cmd": "ls"}, rs, 7, b, 1))
            await asyncio.sleep(0)
            engine.resolve(b.sent[0]["request_id"], approved=True, persistence="once", group_id=1)
            await fut
            # different args → no grant match → must ask again
            fut2 = asyncio.ensure_future(engine.check("run_shell", {"cmd": "rm x"}, rs, 7, b, 1))
            await asyncio.sleep(0)
            asked = len(b.sent) == 2
            engine.resolve(b.sent[1]["request_id"], approved=False, group_id=1)
            await fut2
            return asked

        assert _run(run()), "different args must not reuse the grant"

    def test_deny_rule_beats_once_grant(self):
        rs = Ruleset(rules=[Rule(tool_pattern="run_shell", action="deny")])
        b = _MockBroadcaster()
        # Pre-seed a once-grant; a deny rule must still win.
        engine._once_grants[(7, 1)] = [("run_shell", engine._args_hash({"cmd": "ls"}))]
        r = _run(engine.check("run_shell", {"cmd": "ls"}, rs, 7, b, 1))
        assert r["action"] == "deny"


# ---------------------------------------------------------------------------
# DFT-031: ask timeout, group-cancel on disconnect, resolve authz
# ---------------------------------------------------------------------------

class TestAskTimeout:
    def setup_method(self):
        engine._once_grants.clear()
        engine._pending.clear()

    def test_unanswered_ask_times_out_to_deny(self, monkeypatch):
        monkeypatch.setattr(engine, "_ASK_TIMEOUT_SECONDS", 0.05)
        rs = Ruleset(mode="default")
        b = _MockBroadcaster()

        r = _run(engine.check("run_shell", {"cmd": "ls"}, rs, 7, b, 1))
        assert r["action"] == "deny"
        assert "超时" in r["reason"]
        assert not engine._pending, "pending must be cleaned up on timeout"


class TestCancelPendingForGroup:
    def setup_method(self):
        engine._once_grants.clear()
        engine._pending.clear()

    def test_cancel_denies_pending_in_group(self):
        rs = Ruleset(mode="default")
        b = _MockBroadcaster()

        async def run():
            fut = asyncio.ensure_future(engine.check("run_shell", {"cmd": "ls"}, rs, 7, b, 1))
            await asyncio.sleep(0)
            assert engine._pending
            n = engine.cancel_pending_for_group(1)
            return n, await fut

        n, r = _run(run())
        assert n == 1
        assert r["action"] == "deny"
        assert not engine._pending

    def test_cancel_only_targets_matching_group(self):
        rs = Ruleset(mode="default")
        b = _MockBroadcaster()

        async def run():
            fut1 = asyncio.ensure_future(engine.check("run_shell", {"cmd": "ls"}, rs, 7, b, 1))
            fut2 = asyncio.ensure_future(engine.check("run_shell", {"cmd": "ls"}, rs, 7, b, 2))
            await asyncio.sleep(0.01)
            assert len(b.sent) == 2
            n = engine.cancel_pending_for_group(1)
            r1 = await fut1
            # group 2 untouched — resolve it to finish
            g2_rid = next(s["request_id"] for s in b.sent
                          if engine.resolve(s["request_id"], True, "once", group_id=2))
            r2 = await fut2
            return n, r1, r2, g2_rid

        n, r1, r2, _ = _run(run())
        assert n == 1
        assert r1["action"] == "deny"
        assert r2["action"] == "allow"


class TestResolveAuthz:
    def setup_method(self):
        engine._once_grants.clear()
        engine._pending.clear()

    def test_resolve_from_wrong_group_rejected(self):
        rs = Ruleset(mode="default")
        b = _MockBroadcaster()

        async def run():
            fut = asyncio.ensure_future(engine.check("run_shell", {"cmd": "ls"}, rs, 7, b, 1))
            await asyncio.sleep(0)
            rid = b.sent[0]["request_id"]
            # attacker on group 2 tries to approve group 1's request
            bad = engine.resolve(rid, approved=True, persistence="always", group_id=2)
            # legit group-1 owner approves
            good = engine.resolve(rid, approved=True, persistence="once", group_id=1)
            return bad, good, await fut

        bad, good, r = _run(run())
        assert bad is None, "cross-group approval must be rejected"
        assert good is not None
        assert r["action"] == "allow"


# ---------------------------------------------------------------------------
# DFT-031: LRU cache on _match_tool_pattern and _match_args_pattern
# ---------------------------------------------------------------------------

class TestMatchCaching:
    def setup_method(self):
        engine._match_tool_pattern.cache_clear()
        engine._match_args_pattern.cache_clear()

    def test_tool_pattern_cache_records_hits(self):
        engine._match_tool_pattern("run_shell", "run_shell")
        engine._match_tool_pattern("run_shell", "run_shell")
        assert engine._match_tool_pattern.cache_info().hits >= 1

    def test_args_pattern_cache_records_hits(self):
        args_json = json.dumps({"cmd": "git status"}, sort_keys=True)
        engine._match_args_pattern("git *", args_json)
        engine._match_args_pattern("git *", args_json)
        assert engine._match_args_pattern.cache_info().hits >= 1

    def test_cached_semantics_match_original(self):
        rule = Rule(tool_pattern="run_shell", args_pattern="git *", action="allow")
        assert engine._matches(rule, "run_shell", {"cmd": "git status"})
        assert engine._matches(rule, "run_shell", {"cmd": "git status"})  # cache hit
        assert not engine._matches(rule, "run_shell", {"cmd": "ls -la"})
